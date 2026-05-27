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
  is_ephemeral boolean not null default false
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
  is_ephemeral boolean
);

-- ---------------------------------------------------------------------------
-- Indexes
-- ---------------------------------------------------------------------------
create index if not exists idx_file_locks_acquired_at
  on file_locks(acquired_at);
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
-- This function attempts to insert a lock for file_path. If a lock already
-- exists it will only be replaced by the same owner (renewals). There is
-- no automatic expiry-based replacement: locks persist until explicitly
-- released. Returns status and token.
-- and token.
--
-- Usage (RPC):
--   select * from acquire_lock('path', 'alice', 'editing', 'uuid-token');
create or replace function acquire_lock(
  p_file_path text,
  p_developer_id text,
  p_branch_name text,
  p_reason text,
  p_lock_token text,
  p_is_ephemeral boolean default false
) returns table(status text, lock_token text, owner text) as $$
declare
  rec record;
begin
  -- Try to insert; on conflict update only when expired or same owner
  -- Insert without an expires_at value; locks persist until released.
  insert into file_locks(file_path, developer_id, branch_name, lock_token, reason, acquired_at, is_ephemeral)
  values (p_file_path, p_developer_id, p_branch_name, p_lock_token, p_reason, now(), p_is_ephemeral)
  on conflict (file_path) do update
    set developer_id = excluded.developer_id,
        branch_name = excluded.branch_name,
        lock_token = excluded.lock_token,
        reason = excluded.reason,
        acquired_at = now(),
        is_ephemeral = excluded.is_ephemeral
    -- Do not replace another developer's lock. Only allow update when the
    -- existing lock belongs to the requester (renewal).
    where file_locks.developer_id = excluded.developer_id
  returning file_locks.lock_token, file_locks.developer_id into rec;

  if found then
    return query select 'ok'::text, rec.lock_token::text, rec.developer_id::text;
  end if;

  -- No row returned => conflict, return current owner/token
  select fl.lock_token, fl.developer_id into rec from file_locks fl where fl.file_path = p_file_path;
  return query select 'conflict'::text, rec.lock_token::text, rec.developer_id::text;
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
    acquired_at, released_at, outcome, is_ephemeral
  ) values (
    OLD.file_path, OLD.developer_id, OLD.lock_token, OLD.branch_name, OLD.reason,
    OLD.acquired_at, now(), 'released', OLD.is_ephemeral
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
