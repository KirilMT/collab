-- =============================================================================
-- Supabase schema for collaborative file locks
-- Run this SQL in your Supabase project's SQL Editor to create all tables,
-- functions, policies, and triggers needed for collaborative file locking.
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Tables
-- ---------------------------------------------------------------------------
create table if not exists file_locks (
  file_path text primary key,
  developer_id text not null,
  lock_token text not null,
  branch_name text,
  reason text,
  acquired_at timestamptz not null default now(),
  is_ephemeral boolean not null default false,
  agent_id text,
  agent_label text,
  -- ``origin`` records WHO performed the change: a human developer or an AI
  -- agent. It is the authoritative attribution signal for the dashboard and is
  -- independent of ``agent_id`` (which is the unique-but-internal owner key).
  origin text not null default 'human',
  -- ``agent_kind`` is the AI runtime family (cursor, claude-code, copilot, ...)
  -- used purely for friendly display (icon/name). Never shown as a raw id.
  agent_kind text
);

create table if not exists file_locks_history (
  id bigserial primary key,
  file_path text,
  developer_id text,
  lock_token text,
  branch_name text,
  reason text,
  acquired_at timestamptz,
  released_at timestamptz,
  outcome text,
  is_ephemeral boolean,
  agent_id text,
  agent_label text,
  origin text,
  agent_kind text
);

-- ---------------------------------------------------------------------------
-- Idempotent column upgrades for existing installs (safe to re-run).
-- These let consumer projects adopt strict attribution without recreating
-- their tables or losing data.
-- ---------------------------------------------------------------------------
alter table file_locks
  add column if not exists agent_id text,
  add column if not exists agent_label text,
  add column if not exists origin text not null default 'human',
  add column if not exists agent_kind text;

alter table file_locks_history
  add column if not exists agent_id text,
  add column if not exists agent_label text,
  add column if not exists origin text,
  add column if not exists agent_kind text;

-- Backfill attribution for rows created before strict attribution existed:
-- a non-null agent_id implies the lock was created by an AI agent. Note that
-- `origin` is added as NOT NULL DEFAULT 'human', so existing agent rows are
-- initially filled with 'human' and MUST be corrected here (do not gate on
-- `origin is null`). These updates are idempotent and safe to re-run.
update file_locks
  set origin = 'agent'
  where agent_id is not null and origin is distinct from 'agent';
update file_locks
  set origin = 'human'
  where agent_id is null and origin is distinct from 'human';
update file_locks_history
  set origin = case when agent_id is not null then 'agent' else 'human' end
  where origin is null or origin not in ('human', 'agent');

-- ---------------------------------------------------------------------------
-- PR-aware persistent claims (opt-in via COLLAB_PR_CLAIMS=1 on the client).
-- A "claim" is an ordinary lock that survives ``git push``: instead of being
-- released on push, the files changed on the pushed branch are retained as
-- claims (``is_pr_claim = true``) tied to ``claim_branch`` until that branch is
-- merged into the base or deleted on the remote (released by the client
-- reconciler) -- or, as a guaranteed fallback, expired by ``release_stale_claims``
-- below. This extends cross-developer edit-time protection to open, pushed PR
-- branches. Adding these columns is safe and idempotent; the runtime tolerates
-- them being absent and simply behaves as before.
alter table file_locks
  add column if not exists is_pr_claim boolean not null default false,
  add column if not exists claim_branch text,
  add column if not exists claimed_at timestamptz;

alter table file_locks_history
  add column if not exists is_pr_claim boolean,
  add column if not exists claim_branch text,
  add column if not exists claimed_at timestamptz;

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
create index if not exists idx_file_locks_acquired_at
  on file_locks(acquired_at);
create index if not exists idx_file_locks_owner
  on file_locks(developer_id, agent_id);
-- Speeds up claim reconciliation and stale-claim expiry.
create index if not exists idx_file_locks_pr_claims
  on file_locks(claim_branch)
  where is_pr_claim;
-- Note: expiry semantics are intentionally disabled. Locks persist until
-- explicitly released; no automatic time-based replacement is enforced.
create index if not exists idx_file_locks_history_developer
  on file_locks_history(developer_id);
create index if not exists idx_file_locks_history_released_at
  on file_locks_history(released_at);

-- ---------------------------------------------------------------------------
-- Row Level Security
-- ---------------------------------------------------------------------------
alter table file_locks enable row level security;
alter table file_locks_history enable row level security;

-- Anyone with the anon key can read all locks (needed for dashboard + warnings)
create policy "anyone can read locks"
  on file_locks for select
  using (true);

-- A developer can insert a new lock
create policy "owner can acquire lock"
  on file_locks for insert
  with check (true);

-- A developer can update their own lock (or when JWT is empty / service role)
-- NOTE: Because the collab system uses shared API keys (not per-user JWT),
-- fine-grained ownership enforcement happens at the application level.
create policy "owner can update own lock"
  on file_locks for update
  using (true);

-- A developer can delete (release) their own lock (or service role).
-- NOTE: Because the collab system uses shared API keys (not per-user JWT),
-- fine-grained ownership enforcement happens at the application level
-- (lock_client.py and dashboard). Non-admin users can only release their
-- own locks; admin users (with service role key) can release any lock.
create policy "anyone can release locks"
  on file_locks for delete
  using (true);

-- History table: read-only for all, insert via trigger only
create policy "anyone can read history"
  on file_locks_history for select
  using (true);

create policy "system can insert history"
  on file_locks_history for insert
  with check (true);

-- ---------------------------------------------------------------------------
-- Enable Realtime on file_locks table
-- ---------------------------------------------------------------------------
alter publication supabase_realtime add table file_locks;

-- ---------------------------------------------------------------------------
-- Atomic lock acquisition function (RPC)
-- ---------------------------------------------------------------------------
-- This function attempts to insert a lock for file_path. Attribution is
-- STICKY toward the AI agent (#169): an agent claim upgrades/renews the lock to
-- ``origin=agent``, and a human acquire (background watcher OR explicit commit)
-- never downgrades an existing agent lock. Same-developer renewal always
-- succeeds EXCEPT when a *different* agent of the same developer already holds
-- the file -- that still conflicts so concurrent worktree edits are surfaced at
-- edit time (#150/#153). Cross-developer acquisition always conflicts.
--
-- Returns status, lock_token, owner, agent_id, agent_label, agent_kind, and
-- the *previous* branch name (existing_branch) when a same-developer renewal
-- happened on a different branch — the client uses this to emit a cross-branch
-- advisory warning.
--
-- DROP before CREATE OR REPLACE so the return type can change across versions.
-- PostgreSQL forbids changing the OUT-parameter row type of an existing function
-- even with CREATE OR REPLACE (error 42P13).
drop function if exists acquire_lock(
  text, text, text, text, text, boolean, text, text, text, text
);
create or replace function acquire_lock(
  p_file_path text,
  p_developer_id text,
  p_branch_name text,
  p_reason text,
  p_lock_token text,
  p_is_ephemeral boolean default false,
  p_agent_id text default null,
  p_agent_label text default null,
  p_origin text default 'human',
  p_agent_kind text default null
) returns table(
  status text,
  lock_token text,
  owner text,
  agent_id text,
  agent_label text,
  agent_kind text,
  existing_branch text
) as $$
declare
  rec record;
  _old_branch text;
begin
  -- Snapshot the current lock's branch before the upsert so we can report
  -- cross-branch renewals.
  select fl.branch_name into _old_branch
  from file_locks fl
  where fl.file_path = p_file_path
    and fl.developer_id = p_developer_id;

  insert into file_locks(
    file_path, developer_id, branch_name, lock_token, reason,
    acquired_at, is_ephemeral, agent_id, agent_label, origin, agent_kind
  )
  values (
    p_file_path, p_developer_id, p_branch_name, p_lock_token, p_reason,
    now(), p_is_ephemeral, p_agent_id, p_agent_label,
    coalesce(p_origin, 'human'), p_agent_kind
  )
  on conflict (file_path) do update
    set developer_id = excluded.developer_id,
        branch_name  = excluded.branch_name,
        lock_token   = excluded.lock_token,
        is_ephemeral = excluded.is_ephemeral,
        -- STICKY ATTRIBUTION (#169): an agent claim upgrades/renews the lock to
        -- ``origin=agent``; a human acquire (background watcher OR explicit
        -- commit) NEVER downgrades an existing agent lock. This makes "who
        -- edited" atomic and race-free at the only serialization point (the
        -- upsert), instead of relying on client-side timing that lost the race.
        agent_id = case
                     when excluded.agent_id is not null then excluded.agent_id
                     else file_locks.agent_id
                   end,
        origin = case
                   when excluded.agent_id is not null then 'agent'
                   when file_locks.agent_id is not null then file_locks.origin
                   else coalesce(excluded.origin, 'human')
                 end,
        agent_label = case
                        when excluded.agent_id is not null then excluded.agent_label
                        else file_locks.agent_label
                      end,
        agent_kind = case
                       when excluded.agent_id is not null then excluded.agent_kind
                       else file_locks.agent_kind
                     end,
        -- Preserve the AI-agent reason when a human auto-lock renews an agent
        -- lock (do not overwrite "AI agent edit" with "Auto-Watch Sync").
        reason = case
                   when excluded.agent_id is null
                        and file_locks.agent_id is not null
                     then file_locks.reason
                   else excluded.reason
                 end,
        -- Never reset acquisition time on renewal: durations stay honest and a
        -- background poll cannot make a long-held lock look brand-new (#170).
        acquired_at = file_locks.acquired_at
    where file_locks.developer_id = excluded.developer_id
      -- Same developer may renew/upgrade their own lock. The ONLY same-developer
      -- block is cross-agent: two DIFFERENT agents of one developer editing the
      -- same file still conflict (edit-time signal from #153). Cross-developer
      -- conflicts are blocked by the developer_id equality above.
      and not (
        file_locks.agent_id is not null
        and excluded.agent_id is not null
        and file_locks.agent_id is distinct from excluded.agent_id
      )
  returning file_locks.lock_token, file_locks.developer_id, file_locks.agent_id,
            file_locks.agent_label, file_locks.agent_kind into rec;

  if found then
    return query select
      'ok'::text,
      rec.lock_token::text,
      rec.developer_id::text,
      rec.agent_id::text,
      rec.agent_label::text,
      rec.agent_kind::text,
      case
        when _old_branch is not null and _old_branch <> p_branch_name
        then _old_branch
        else null
      end::text;
    return;
  end if;

  select fl.lock_token, fl.developer_id, fl.agent_id,
         fl.agent_label, fl.agent_kind
  into rec
  from file_locks fl where fl.file_path = p_file_path;
  return query select
    'conflict'::text,
    rec.lock_token::text,
    rec.developer_id::text,
    rec.agent_id::text,
    rec.agent_label::text,
    rec.agent_kind::text,
    null::text;
end;
$$ language plpgsql security definer;

-- ---------------------------------------------------------------------------
-- PR-claim retention on push (RPC)
-- ---------------------------------------------------------------------------
-- Used by the pre-push hook when COLLAB_PR_CLAIMS=1. Atomically:
--   * retains (promotes to a PR claim) this developer's locks for the files that
--     are still part of the pushed branch (``p_keep_paths``), tying them to
--     ``p_branch`` and stamping ``claimed_at`` -- WITHOUT touching attribution
--     columns (origin/agent_id/agent_label), so dashboard attribution is intact;
--   * releases every other lock held by this developer (today's behavior for the
--     rest). Returns the number of locks released (claims retained are not
--     counted). Developer-scoped: never touches other developers' locks.
create or replace function release_all_except(
  p_developer_id text,
  p_keep_paths text[],
  p_branch text
) returns integer as $$
declare
  v_released integer;
begin
  update file_locks
    set is_pr_claim = true,
        claim_branch = p_branch,
        claimed_at = now()
    where developer_id = p_developer_id
      and file_path = any(coalesce(p_keep_paths, array[]::text[]));

  with deleted as (
    delete from file_locks
    where developer_id = p_developer_id
      and not (file_path = any(coalesce(p_keep_paths, array[]::text[])))
    returning 1
  )
  select count(*) into v_released from deleted;

  return coalesce(v_released, 0);
end;
$$ language plpgsql security definer;

-- ---------------------------------------------------------------------------
-- Auto-history trigger: log releases to history table
-- ---------------------------------------------------------------------------
create or replace function log_lock_release()
returns trigger as $$
begin
  insert into file_locks_history(
    file_path, developer_id, lock_token, branch_name, reason,
    acquired_at, released_at, outcome, is_ephemeral, agent_id, agent_label,
    origin, agent_kind, is_pr_claim, claim_branch, claimed_at
  ) values (
    OLD.file_path, OLD.developer_id, OLD.lock_token, OLD.branch_name, OLD.reason,
    OLD.acquired_at, now(), 'released', OLD.is_ephemeral, OLD.agent_id, OLD.agent_label,
    OLD.origin, OLD.agent_kind, OLD.is_pr_claim, OLD.claim_branch, OLD.claimed_at
  );

  -- Automatic retention: keep history bounded without manual intervention.
  perform prune_lock_history(30);

  return OLD;
end;
$$ language plpgsql security definer;

create or replace trigger on_lock_release
  before delete on file_locks
  for each row execute function log_lock_release();

-- ---------------------------------------------------------------------------
-- History retention utilities (default: 30 days)
-- ---------------------------------------------------------------------------
create or replace function prune_lock_history(p_retention_days integer default 30)
returns bigint as $$
declare
  v_deleted bigint;
begin
  if p_retention_days < 1 then
    raise exception 'p_retention_days must be >= 1';
  end if;

  with deleted as (
    delete from file_locks_history
    where coalesce(released_at, acquired_at) < now() - make_interval(days => p_retention_days)
    returning 1
  )
  select count(*) into v_deleted from deleted;

  return coalesce(v_deleted, 0);
end;
$$ language plpgsql security definer;

-- Optional daily scheduler (pg_cron): keeps retention active even during quiet periods.
-- Safe to rerun. If pg_cron is unavailable, this block exits without failing schema setup.
do $retention$
begin
  if to_regclass('cron.job') is not null then
    perform cron.unschedule(jobid)
    from cron.job
    where jobname = 'prune_file_locks_history';

    perform cron.schedule(
      'prune_file_locks_history',
      '17 3 * * *',
      $job$select prune_lock_history(30);$job$
    );
  end if;
exception
  when undefined_function then
    null;
  when undefined_table then
    null;
end;
$retention$;

-- ---------------------------------------------------------------------------
-- PR-claim expiry (guaranteed release path; default: 30 days)
-- ---------------------------------------------------------------------------
-- The client reconciler releases claims promptly when a branch is merged or
-- deleted, but it only runs while an owner's daemon/pre-push runs. This DB-side
-- expiry guarantees a claim can never block other developers forever even if its
-- owner never comes back. Keyed on claimed_at (set when the claim is created and
-- left untouched by ordinary lock renewals).
create or replace function release_stale_claims(p_days integer default 30)
returns bigint as $$
declare
  v_deleted bigint;
begin
  if p_days < 1 then
    raise exception 'p_days must be >= 1';
  end if;

  with deleted as (
    delete from file_locks
    where is_pr_claim = true
      and coalesce(claimed_at, acquired_at) < now() - make_interval(days => p_days)
    returning 1
  )
  select count(*) into v_deleted from deleted;

  return coalesce(v_deleted, 0);
end;
$$ language plpgsql security definer;

-- Optional daily scheduler (pg_cron) for claim expiry. Safe to rerun.
do $claims$
begin
  if to_regclass('cron.job') is not null then
    perform cron.unschedule(jobid)
    from cron.job
    where jobname = 'release_stale_file_claims';

    perform cron.schedule(
      'release_stale_file_claims',
      '23 3 * * *',
      $job$select release_stale_claims(30);$job$
    );
  end if;
exception
  when undefined_function then
    null;
  when undefined_table then
    null;
end;
$claims$;
