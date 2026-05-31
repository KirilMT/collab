/**
 * Deterministic dense dashboard fixtures (production-scale row counts).
 */

const {
  formatDurationMinutes,
} = require("../../../collab/dashboard/dashboard-format");

const FIXED_NOW = "2026-01-15T14:30:00.000Z";
const ACTIVE_LOCK_COUNT = 16;
const HISTORY_TOTAL = 30;
const PAGE_SIZE = 25;

const SEED_CONFIG = {
  url: "https://e2e-seed.supabase.co",
  anonKey: "e2e-seed-anon-key",
  user: "alice",
};

const REPO_PATHS = [
  ".github/workflows/ci.yml",
  "collab/lock_client.py",
  "collab/dashboard/index.html",
  "collab/dashboard/dashboard-format.js",
  "tests/frontend/playwright/dashboard.spec.js",
  "tests/frontend/unit/dashboard-format.test.js",
  "docs/API.md",
  "docs/ARCHITECTURE.md",
  "AGENTS.md",
  "README.md",
  ".agents/skills/testing-workflow/SKILL.md",
  "supabase/schema.sql",
  "scripts/validate_code.py",
  "playwright.config.js",
  "package.json",
  "collab/main.py",
];

function buildDenseActiveLocks() {
  const base = new Date(FIXED_NOW).getTime();
  return REPO_PATHS.slice(0, ACTIVE_LOCK_COUNT).map((file_path, index) => ({
    id: index + 1,
    file_path,
    developer_id: index % 3 === 1 ? "bob" : "alice",
    branch_name: index % 2 === 0 ? "main" : "feat/e2e",
    reason: "Auto-Watch Sync",
    acquired_at: new Date(base - (index + 1) * 15 * 60 * 1000).toISOString(),
    is_ephemeral: false,
  }));
}

function buildDenseHistory() {
  const rows = [];
  for (let i = 0; i < HISTORY_TOTAL; i += 1) {
    const dayOffset = Math.floor(i / 6);
    const acquired = new Date("2026-01-15T08:00:00.000Z");
    acquired.setUTCDate(acquired.getUTCDate() - dayOffset);
    acquired.setUTCHours(8 - (i % 6), (i % 4) * 10, 0, 0);
    const released = new Date(
      acquired.getTime() + (20 + (i % 5) * 10) * 60 * 1000,
    );
    rows.push({
      id: 500 - i,
      file_path: `tests/backend/unit/sample_${String(i).padStart(2, "0")}.py`,
      developer_id: i % 3 === 0 ? "carol" : i % 3 === 1 ? "bob" : "alice",
      branch_name: "main",
      reason: "Auto-Watch Sync",
      acquired_at: acquired.toISOString(),
      released_at: released.toISOString(),
      outcome: "released",
      is_ephemeral: false,
    });
  }
  return rows.sort((a, b) => b.id - a.id);
}

function computeExpectedStats(activeLocks, history, nowIso) {
  const today = new Date(nowIso).toDateString();
  const todayReleases = history.filter((h) => {
    if (!h.released_at) {
      return false;
    }
    return new Date(h.released_at).toDateString() === today;
  }).length;

  const durations = history
    .filter((h) => h.acquired_at && h.released_at)
    .map(
      (h) =>
        (new Date(h.released_at).getTime() -
          new Date(h.acquired_at).getTime()) /
        60000,
    )
    .filter((d) => Number.isFinite(d) && d >= 0);

  const avg = durations.length
    ? Math.round(durations.reduce((acc, v) => acc + v, 0) / durations.length)
    : 0;

  return {
    active: String(activeLocks.length),
    releases: String(todayReleases),
    avg: formatDurationMinutes(avg),
  };
}

const SEED_ACTIVE_LOCKS = buildDenseActiveLocks();
const SEED_HISTORY = buildDenseHistory();
const EXPECTED_STATS = computeExpectedStats(
  SEED_ACTIVE_LOCKS,
  SEED_HISTORY,
  FIXED_NOW,
);

const SEED_PAYLOAD = {
  config: SEED_CONFIG,
  activeLocks: SEED_ACTIVE_LOCKS,
  history: SEED_HISTORY,
};

module.exports = {
  FIXED_NOW,
  ACTIVE_LOCK_COUNT,
  HISTORY_TOTAL,
  PAGE_SIZE,
  SEED_CONFIG,
  SEED_ACTIVE_LOCKS,
  SEED_HISTORY,
  EXPECTED_STATS,
  SEED_PAYLOAD,
  buildDenseActiveLocks,
  buildDenseHistory,
  computeExpectedStats,
};
