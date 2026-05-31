/**
 * E2E + Visual tests for the Collaborative Lock Dashboard.
 *
 * These tests run against the exact static HTML served by both the real
 * `collab dashboard` command and the Playwright webServer config.
 *
 * ---------------------------------------------------------------------------
 * DETERMINISTIC TEST DATA / ENVIRONMENT
 * ---------------------------------------------------------------------------
 * The dashboard is a static HTML page that talks directly to Supabase via
 * `supabase-js` (no local backend to seed). For stable E2E and visual tests we
 * inject a deterministic in-page data layer. loadSupabaseClient() checks
 * `if (window.supabase)` first, so we inject:
 *   1. window.__SUPABASE_CONFIG__  -> enables the main dashboard view.
 *   2. window.supabase.createClient -> returns fixed fixture rows for
 *      file_locks / file_locks_history.
 * Combined with page.clock.setFixedTime (held-times, durations, stats, sync
 * timestamp) and the UTC timezone from playwright.config, the UI is identical
 * on every run without cloud credentials or network.
 *
 * Visual stability: addInitScript for stable body height, frozen clock,
 * fullPage snapshots with animations disabled, data-testid selectors.
 *
 * Run locally (after `npm install` in repo root):
 *   npm run test:frontend:e2e:fast     # mock + contract (parallel, no network)
 *   npm run test:frontend:e2e:ci       # CI parity (chromium + @live)
 *   npm run test:frontend:e2e:live     # live Supabase smoke only
 *   npm run test:frontend:e2e:firefox  # optional firefox visual baselines
 *
 * Snapshots live next to this file in the -snapshots/ directory.
 */

const { test, expect } = require("@playwright/test");
const {
  skipIfFeatureDisabled,
  hasSupabaseCredentials,
  playwrightLiveDashboardExists,
} = require("./test-utils");

// Feature gate: entire suite respects DASHBOARD_ENABLED (defaults to true).
skipIfFeatureDisabled(test, "DASHBOARD");

const {
  FIXED_NOW,
  ACTIVE_LOCK_COUNT,
  HISTORY_TOTAL,
  PAGE_SIZE,
  SEED_CONFIG,
  SEED_PAYLOAD,
  EXPECTED_STATS,
} = require("./dashboard-seed-data");

/**
 * Runs in the browser BEFORE the dashboard scripts. Installs a stable height
 * and a fake `window.supabase` with a mutable in-memory store so flows like
 * release, sync, and fetch errors behave like production.
 */
function injectSupabaseSeed(payload) {
  const addStableHeight = function () {
    const style = document.createElement("style");
    style.id = "pw-visual-test-height";
    style.textContent = "body { min-height: 2800px !important; }";
    document.head.appendChild(style);
  };
  if (document.head) {
    addStableHeight();
  } else {
    document.addEventListener("DOMContentLoaded", addStableHeight);
  }

  window.__SUPABASE_CONFIG__ = payload.config;

  const store = {
    activeLocks: JSON.parse(JSON.stringify(payload.activeLocks)),
    history: JSON.parse(JSON.stringify(payload.history)),
    failNextLocksFetch: Boolean(payload.failNextLocksFetch),
  };

  const makeBuilder = function (table) {
    const state = { isDelete: false, filters: {} };
    const resolveResult = function () {
      if (state.isDelete) {
        if (table === "file_locks") {
          store.activeLocks = store.activeLocks.filter(function (lock) {
            if (
              state.filters.file_path &&
              lock.file_path !== state.filters.file_path
            ) {
              return true;
            }
            if (
              state.filters.developer_id &&
              lock.developer_id !== state.filters.developer_id
            ) {
              return true;
            }
            if (
              Object.prototype.hasOwnProperty.call(state.filters, "agent_id")
            ) {
              const lockAgent = lock.agent_id || null;
              if (state.filters.agent_id !== lockAgent) {
                return true;
              }
            }
            return false;
          });
        }
        return { data: null, error: null };
      }

      if (table === "file_locks") {
        if (store.failNextLocksFetch) {
          store.failNextLocksFetch = false;
          return {
            data: null,
            error: { message: "Simulated fetch failure" },
          };
        }
        return { data: store.activeLocks, error: null };
      }
      if (table === "file_locks_history") {
        return { data: store.history, error: null };
      }
      return { data: [], error: null };
    };
    const builder = {
      select: function () {
        return builder;
      },
      neq: function () {
        return builder;
      },
      eq: function (column, value) {
        if (state.isDelete) {
          state.filters[column] = value;
        }
        return builder;
      },
      is: function (column, value) {
        if (state.isDelete && value === "null") {
          state.filters[column] = null;
        }
        return builder;
      },
      order: function () {
        return builder;
      },
      limit: function () {
        return builder;
      },
      range: function (from, to) {
        if (table === "file_locks_history" && !state.isDelete) {
          state.rangeFrom = from;
          state.rangeTo = to;
        }
        return builder;
      },
      delete: function () {
        state.isDelete = true;
        return builder;
      },
      then: function (onFulfilled, onRejected) {
        let result = resolveResult();
        if (
          table === "file_locks_history" &&
          !state.isDelete &&
          typeof state.rangeFrom === "number"
        ) {
          const slice = store.history.slice(state.rangeFrom, state.rangeTo + 1);
          result = { data: slice, error: null };
        }
        return Promise.resolve(result).then(onFulfilled, onRejected);
      },
    };
    return builder;
  };

  const channel = {
    on: function () {
      return channel;
    },
    subscribe: function () {
      return channel;
    },
    unsubscribe: function () {
      return Promise.resolve("ok");
    },
  };

  window.supabase = {
    createClient: function () {
      return {
        from: function (table) {
          return makeBuilder(table);
        },
        channel: function () {
          return channel;
        },
        removeChannel: function () {
          return Promise.resolve("ok");
        },
      };
    },
  };
}

/** Remove any mock client so the real supabase-js CDN can load. */
function clearMockSupabaseClient() {
  delete window.supabase;
}

// ===========================================================================
// POPULATED, WORKING DASHBOARD (seeded deterministic data) — the real UX.
// ===========================================================================
test.describe("Collaborative Lock Dashboard — populated (seeded data)", () => {
  test.beforeEach(async ({ page }) => {
    // Freeze the clock so durations / "today" / sync timestamp are stable.
    await page.clock.setFixedTime(new Date(FIXED_NOW));
    // Seed the in-page data layer before any dashboard script runs.
    await page.addInitScript(injectSupabaseSeed, SEED_PAYLOAD);

    await page.goto("/", { waitUntil: "domcontentloaded" });

    // Wait until the dashboard has rendered the seeded data (deterministic).
    await expect(page.getByTestId("stat-active")).toHaveText(
      EXPECTED_STATS.active,
    );
  });

  test("renders the main working view with correct seeded stats", async ({
    page,
  }) => {
    // Main view is active; setup view is hidden.
    await expect(page.getByTestId("setup-view")).toHaveClass(/hidden/);
    await expect(page.getByTestId("locks-page")).not.toHaveClass(/hidden/);

    // Logged-in developer is shown in the header.
    await expect(page.getByTestId("user-info")).toContainText("alice");

    // Stat cards reflect the seeded data exactly.
    await expect(page.getByTestId("stat-active")).toHaveText(
      EXPECTED_STATS.active,
    );
    await expect(page.getByTestId("stat-releases")).toHaveText(
      EXPECTED_STATS.releases,
    );
    await expect(page.getByTestId("stat-avg")).toHaveText(EXPECTED_STATS.avg);

    // On the locks view the active tab (Active Locks) is disabled by design;
    // History + Sync remain interactive.
    await expect(page.getByTestId("nav-locks")).toBeDisabled();
    await expect(page.getByTestId("nav-history")).toBeEnabled();
    await expect(page.getByTestId("sync-btn")).toBeEnabled();
  });

  test("renders active lock rows with owner/non-owner actions", async ({
    page,
  }) => {
    const rows = page.getByTestId("active-locks-body").locator("tr");
    await expect(rows).toHaveCount(ACTIVE_LOCK_COUNT);

    // Owner (alice) rows expose a "Release" button; non-owner (bob) is "Locked".
    await expect(
      page
        .getByTestId("active-locks-body")
        .getByRole("button", { name: "Release", exact: true }),
    ).toHaveCount(11);
    await expect(
      page
        .getByTestId("active-locks-body")
        .getByText("Locked", { exact: true }),
    ).toHaveCount(5);

    await expect(
      page
        .getByTestId("active-locks-body")
        .getByText(".github/workflows/ci.yml"),
    ).toBeVisible();
  });

  test("history view loads first page and paginates on scroll", async ({
    page,
  }) => {
    await page.getByTestId("nav-history").click();

    const rows = page.getByTestId("history-body").locator("tr");
    await expect(rows).toHaveCount(PAGE_SIZE);

    const scrollHost = page.locator("#history-scroll");
    await scrollHost.evaluate((el) => {
      el.scrollTop = el.scrollHeight;
    });

    await expect(rows).toHaveCount(HISTORY_TOTAL);
  });

  test("release modal opens with the correct file and copy", async ({
    page,
  }) => {
    await page
      .getByTestId("active-locks-body")
      .getByRole("button", { name: "Release", exact: true })
      .first()
      .click();

    const modal = page.locator("#releaseModal");
    await expect(modal).toBeVisible();
    await expect(modal).toHaveClass(/show/);
    await expect(page.locator("#modal-title")).toHaveText("Release File Lock");
    await expect(page.locator("#modal-file-path")).toHaveText(
      ".github/workflows/ci.yml",
    );
    await expect(page.locator("#confirm-release-btn")).toHaveText(
      "Confirm Release",
    );
  });

  test("confirm release removes the lock and refreshes stats", async ({
    page,
  }) => {
    await page
      .getByTestId("active-locks-body")
      .getByRole("button", { name: "Release", exact: true })
      .first()
      .click();

    const modal = page.locator("#releaseModal");
    await expect(modal).toHaveClass(/show/);
    await page.locator("#confirm-release-btn").click();

    await expect(modal).not.toHaveClass(/show/);
    await expect(page.getByTestId("stat-active")).toHaveText(
      String(ACTIVE_LOCK_COUNT - 1),
    );
    await expect(
      page.getByTestId("active-locks-body").locator("tr"),
    ).toHaveCount(ACTIVE_LOCK_COUNT - 1);
    await expect(
      page
        .getByTestId("active-locks-body")
        .getByText(".github/workflows/ci.yml"),
    ).toHaveCount(0);
  });

  test("cancel dismisses the release modal without changing row count", async ({
    page,
  }) => {
    await page
      .getByTestId("active-locks-body")
      .getByRole("button", { name: "Release", exact: true })
      .first()
      .click();

    const modal = page.locator("#releaseModal");
    await expect(modal).toHaveClass(/show/);
    await modal.getByRole("button", { name: "Cancel" }).click();

    await expect(modal).not.toHaveClass(/show/);
    await expect(page.getByTestId("stat-active")).toHaveText(
      EXPECTED_STATS.active,
    );
    await expect(
      page.getByTestId("active-locks-body").locator("tr"),
    ).toHaveCount(ACTIVE_LOCK_COUNT);
  });

  test("sync button re-fetches without surfacing a fetch error", async ({
    page,
  }) => {
    await page.getByTestId("sync-btn").click();
    await expect(page.getByTestId("sync-btn")).toBeEnabled();
    await expect(page.getByTestId("stat-active")).toHaveText(
      EXPECTED_STATS.active,
    );
    await expect(
      page
        .getByTestId("active-locks-body")
        .getByText("Unable to fetch active locks."),
    ).toHaveCount(0);
  });

  test("hash route #history opens the history view on load", async ({
    page,
  }) => {
    await page.goto("/#history", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("history-page")).toHaveClass(/active/);
    await expect(page.getByTestId("history-body").locator("tr")).toHaveCount(
      PAGE_SIZE,
    );
  });

  test("shows an error message when the locks fetch fails", async ({
    page,
  }) => {
    await page.addInitScript(injectSupabaseSeed, {
      ...SEED_PAYLOAD,
      failNextLocksFetch: true,
    });
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(
      page
        .getByTestId("active-locks-body")
        .getByText("Unable to fetch active locks."),
    ).toBeVisible();
  });

  // -------------------------------------------------------------------------
  // VISUAL REGRESSION — the populated, working UI the user actually sees.
  // Deterministic seed + frozen clock + UTC tz => pixel-stable, no masking.
  // -------------------------------------------------------------------------
  test("visual — active locks (populated)", async ({ page }) => {
    await expect(
      page.getByTestId("active-locks-body").locator("tr"),
    ).toHaveCount(ACTIVE_LOCK_COUNT);

    await expect(page).toHaveScreenshot(
      "dashboard-active-locks-populated.png",
      {
        fullPage: true,
        animations: "disabled",
      },
    );
  });

  test("visual — lock history (populated)", async ({ page }) => {
    await page.getByTestId("nav-history").click();
    await expect(page.getByTestId("history-body").locator("tr")).toHaveCount(
      PAGE_SIZE,
    );

    await expect(page).toHaveScreenshot("dashboard-history-populated.png", {
      fullPage: true,
      animations: "disabled",
    });
  });

  test("visual — release confirmation modal", async ({ page }) => {
    await page
      .getByTestId("active-locks-body")
      .getByRole("button", { name: "Release", exact: true })
      .first()
      .click();

    const modal = page.locator("#releaseModal");
    await expect(modal).toBeVisible();
    await expect(modal).toHaveClass(/show/);
    // Let the Bootstrap fade settle before snapping the dialog.
    await page.waitForTimeout(250);

    await expect(page.locator("#releaseModal .modal-content")).toHaveScreenshot(
      "dashboard-release-modal.png",
      { animations: "disabled" },
    );
  });
});

// ===========================================================================
// ADMIN FORCE-RELEASE (seeded, service role)
// ===========================================================================
test.describe("Collaborative Lock Dashboard — admin force release (seeded)", () => {
  const adminPayload = {
    config: {
      url: SEED_CONFIG.url,
      anonKey: SEED_CONFIG.anonKey,
      serviceKey: "e2e-seed-service-key",
      user: "alice",
    },
    activeLocks: SEED_PAYLOAD.activeLocks,
    history: SEED_PAYLOAD.history,
  };

  test.beforeEach(async ({ page }) => {
    await page.clock.setFixedTime(new Date(FIXED_NOW));
    await page.addInitScript(injectSupabaseSeed, adminPayload);
    await page.goto("/", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("stat-active")).toHaveText(
      EXPECTED_STATS.active,
    );
  });

  test("admin sees Force Release on another developer's lock", async ({
    page,
  }) => {
    await expect(
      page
        .getByTestId("active-locks-body")
        .getByRole("button", { name: "Force Release", exact: true }),
    ).toHaveCount(5);

    await page
      .getByRole("button", { name: "Force Release", exact: true })
      .first()
      .click();

    await expect(page.locator("#modal-title")).toHaveText(
      "Force Release File Lock",
    );
    await expect(page.locator("#modal-file-path")).toHaveText(
      "collab/lock_client.py",
    );
    await expect(page.locator("#confirm-release-btn")).toHaveText(
      "Confirm Force Release",
    );
  });
});

// ===========================================================================
// LIVE SUPABASE SMOKE (requires .env credentials — same injection as
// `collab dashboard`; skipped only when credentials or generated HTML missing)
// ===========================================================================
test.describe("Collaborative Lock Dashboard — live Supabase smoke", () => {
  test.describe.configure({ tag: "@live", mode: "serial", timeout: 90_000 });

  test.beforeEach(async ({ page }) => {
    test.skip(
      !hasSupabaseCredentials(),
      "Supabase credentials not configured in .env",
    );
    test.skip(
      !playwrightLiveDashboardExists(),
      "Live dashboard HTML was not generated — check globalSetup / .env",
    );

    // Seeded tests freeze time; restore real clock for live network calls.
    await page.clock.setSystemTime(new Date());
    // Safety: drop mock client if any init script leaked (should not on fresh context).
    await page.addInitScript(clearMockSupabaseClient);

    page.on("dialog", (dialog) => dialog.dismiss());

    // Same as `collab dashboard`: config prepended to index.html (globalSetup).
    await page.goto("/playwright-live.html", {
      waitUntil: "domcontentloaded",
    });

    await expect(page.getByTestId("setup-view")).toHaveClass(/hidden/, {
      timeout: 25000,
    });

    await expect(page.getByTestId("active-locks-body")).not.toContainText(
      "Connecting...",
      { timeout: 45000 },
    );
    await expect(page.getByTestId("active-locks-body")).not.toContainText(
      "Unable to fetch active locks.",
      { timeout: 45000 },
    );
    await expect(page.getByTestId("stat-active")).toHaveText(/^\d+$/, {
      timeout: 45000,
    });
  });

  test("connects to Supabase and shows the main dashboard shell", async ({
    page,
  }) => {
    await expect(page.getByTestId("locks-page")).not.toHaveClass(/hidden/);
    await expect(page.getByText("Collaborative Explorer")).toBeVisible();
    await expect(page.getByTestId("nav-history")).toBeEnabled();
    await expect(page.getByTestId("sync-btn")).toBeEnabled();
  });

  test("loads the active locks table without a fetch error row", async ({
    page,
  }) => {
    const statText = await page.getByTestId("stat-active").textContent();
    expect(statText).toMatch(/^\d+$/);

    const bodyText = await page.getByTestId("active-locks-body").innerText();
    expect(bodyText).not.toContain("Unable to fetch active locks");
    expect(bodyText).not.toContain("Initialization failed");
  });

  test("history tab loads data or an empty-state message", async ({ page }) => {
    await page.getByTestId("nav-history").click();
    await expect(page.getByTestId("history-page")).toHaveClass(/active/);

    const body = page.getByTestId("history-body");
    await expect(body).not.toContainText("Loading history...", {
      timeout: 30000,
    });

    const historyText = await body.innerText();
    const hasRows = (await body.locator("tr").count()) > 0;
    const hasEmpty =
      historyText.includes("No history found") ||
      historyText.includes("No history");
    expect(hasRows || hasEmpty).toBe(true);
  });

  test("sync completes without an error dialog", async ({ page }) => {
    let alertSeen = false;
    page.on("dialog", () => {
      alertSeen = true;
    });

    await page.getByTestId("sync-btn").click();
    await expect(page.getByTestId("sync-btn")).toBeEnabled({ timeout: 15000 });
    expect(alertSeen).toBe(false);
  });
});

// ===========================================================================
// FIRST-RUN / NO-CREDENTIALS STATE (functional only, no screenshot).
// Protects the real behavior every developer sees before adding Supabase
// credentials, without baking the empty "setup" screen into a baseline.
// ===========================================================================
test.describe("Collaborative Lock Dashboard — first-run (no credentials)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/", { waitUntil: "domcontentloaded" });
  });

  test("shows the setup view and disables main controls", async ({ page }) => {
    await expect(page.getByTestId("setup-view")).toBeVisible();
    await expect(page.getByTestId("locks-page")).toHaveClass(/hidden/);
    await expect(page.getByTestId("history-page")).toHaveClass(/hidden/);

    await expect(page.getByText("Collaborative Explorer")).toBeVisible();

    await expect(page.getByTestId("nav-locks")).toBeDisabled();
    await expect(page.getByTestId("nav-history")).toBeDisabled();
    await expect(page.getByTestId("sync-btn")).toBeDisabled();

    // Containers still exist in the DOM (used once credentials are present).
    await expect(page.getByTestId("stats-grid")).toBeAttached();
    await expect(page.getByTestId("active-locks-body")).toBeAttached();
    await expect(page.getByTestId("history-body")).toBeAttached();
  });
});
