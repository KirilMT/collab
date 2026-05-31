/**
 * Supabase schema + RLS contract tests for the dashboard.
 * Replaces manual pre-release checks: fails when API shape or policies drift.
 */

const { test, expect } = require("@playwright/test");
const {
  skipIfFeatureDisabled,
  hasSupabaseCredentials,
  buildSupabaseConfigFromEnv,
  supabaseRestHeaders,
} = require("./test-utils");

skipIfFeatureDisabled(test, "DASHBOARD");

const DASHBOARD_LOCK_COLUMNS =
  "file_path,developer_id,branch_name,reason,acquired_at,is_ephemeral";
const DASHBOARD_HISTORY_COLUMNS =
  "file_path,developer_id,branch_name,reason,acquired_at,released_at,outcome,is_ephemeral";

test.describe("Supabase dashboard contract", () => {
  test.beforeEach(() => {
    test.skip(
      !hasSupabaseCredentials(),
      "Supabase credentials not configured in .env or CI env",
    );
  });

  test("file_locks is readable with dashboard columns (RLS select)", async ({
    request,
  }) => {
    const config = buildSupabaseConfigFromEnv();
    const url = `${config.url}/rest/v1/file_locks?select=${DASHBOARD_LOCK_COLUMNS}&is_ephemeral=neq.true&limit=1`;
    const response = await request.get(url, {
      headers: supabaseRestHeaders(config),
    });

    expect(response.status(), await response.text()).toBe(200);
    const rows = await response.json();
    expect(Array.isArray(rows)).toBe(true);
    if (rows.length > 0) {
      expect(rows[0]).toHaveProperty("file_path");
      expect(rows[0]).toHaveProperty("is_ephemeral");
    }
  });

  test("file_locks_history is readable with dashboard columns (RLS select)", async ({
    request,
  }) => {
    const config = buildSupabaseConfigFromEnv();
    const url = `${config.url}/rest/v1/file_locks_history?select=${DASHBOARD_HISTORY_COLUMNS}&is_ephemeral=neq.true&order=id.desc&limit=1`;
    const response = await request.get(url, {
      headers: supabaseRestHeaders(config),
    });

    expect(response.status(), await response.text()).toBe(200);
    const rows = await response.json();
    expect(Array.isArray(rows)).toBe(true);
    if (rows.length > 0) {
      expect(rows[0]).toHaveProperty("released_at");
      expect(rows[0]).toHaveProperty("outcome");
    }
  });

  test("stats query shape matches dashboard refreshLocks()", async ({
    request,
  }) => {
    const config = buildSupabaseConfigFromEnv();
    const historyUrl = `${config.url}/rest/v1/file_locks_history?select=acquired_at,released_at&is_ephemeral=neq.true&order=id.desc&limit=250`;
    const response = await request.get(historyUrl, {
      headers: supabaseRestHeaders(config),
    });

    expect(response.status(), await response.text()).toBe(200);
    const rows = await response.json();
    expect(Array.isArray(rows)).toBe(true);
    for (const row of rows.slice(0, 3)) {
      expect(row).toHaveProperty("acquired_at");
      expect(row).toHaveProperty("released_at");
    }
  });
});
