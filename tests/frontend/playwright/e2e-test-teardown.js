/**
 * E2E Test Global Teardown
 *
 * This script runs AFTER all Playwright tests complete. It ensures:
 * 1. Any process on the dashboard test port is stopped
 * 2. Any stale collab daemon processes are cleaned up
 * 3. Temporary test artifacts under .collab/ are removed
 *
 * Follows the same cleanup pattern as pytest's cleanup_test_artifacts fixture.
 */

const path = require("path");
const fs = require("fs");
const { execSync } = require("child_process");

// Configuration — must match e2e-test-setup.js
const TEST_PORT = 8000;
const PROJECT_ROOT = path.resolve(__dirname, "../../..");
const COLLAB_DIR = path.join(PROJECT_ROOT, ".collab");
const PLAYWRIGHT_LIVE_DASHBOARD = path.join(
  PROJECT_ROOT,
  "collab",
  "dashboard",
  "playwright-live.html",
);

/**
 * Force kill any process listening on port.
 */
function killProcessOnPort(port) {
  try {
    if (process.platform === "win32") {
      const output = execSync(`netstat -ano | findstr :${port}`).toString();
      const lines = output.trim().split("\n");
      let killed = false;

      for (const line of lines) {
        if (line.includes(`:${port}`) && line.includes("LISTENING")) {
          const parts = line.trim().split(/\s+/);
          const pid = parts[parts.length - 1];

          if (pid && /^\d+$/.test(pid) && pid !== "0") {
            console.warn(`🛑 Killing server on port ${port} (PID: ${pid})`);
            try {
              execSync(`taskkill /PID ${pid} /F`);
              killed = true;
            } catch (_err) {
              // Process might have already exited
            }
          }
        }
      }
      return killed;
    } else {
      // Linux/Mac fallback
      const pid = execSync(`lsof -t -i:${port}`).toString().trim();
      if (pid) {
        process.kill(Number(pid));
        return true;
      }
    }
  } catch (_e) {
    // No process found — this is fine
  }
  return false;
}

/**
 * Remove stale collab E2E test lock artifacts.
 * Only removes entries that were created with the e2e test reason marker.
 */
function cleanupCollabTestArtifacts() {
  if (!fs.existsSync(COLLAB_DIR)) {
    return;
  }

  let removed = 0;
  try {
    const entries = fs.readdirSync(COLLAB_DIR);
    for (const entry of entries) {
      const entryPath = path.join(COLLAB_DIR, entry);
      try {
        const stat = fs.statSync(entryPath);
        if (stat.isFile() && entry.endsWith(".lock")) {
          const content = fs.readFileSync(entryPath, "utf8");
          // Only remove locks written by the E2E test suite
          if (content.includes("e2e_test") || content.includes("playwright")) {
            fs.unlinkSync(entryPath);
            removed++;
            console.warn(
              `✅ Removed E2E test lock artifact: ${path.basename(entryPath)}`,
            );
          }
        }
      } catch (_err) {
        // Skip unreadable entries
      }
    }
  } catch (error) {
    console.warn(
      `⚠️  Could not scan .collab/ for test artifacts: ${error.message}`,
    );
  }

  if (removed === 0) {
    console.warn("   No E2E test lock artifacts to clean up.");
  }
}

/**
 * Global teardown function called by Playwright after all tests complete.
 */
async function globalTeardown() {
  console.warn("\n🧹 E2E Test Global Teardown Starting...\n");

  // Kill dashboard server to release port
  killProcessOnPort(TEST_PORT);

  // Wait for process to fully release handles
  await new Promise((resolve) => setTimeout(resolve, 250));

  // Clean up any lock artifacts left by E2E tests
  cleanupCollabTestArtifacts();

  if (fs.existsSync(PLAYWRIGHT_LIVE_DASHBOARD)) {
    fs.unlinkSync(PLAYWRIGHT_LIVE_DASHBOARD);
    console.warn("✅ Removed generated live dashboard HTML.");
  }

  console.warn("\n✅ Global teardown complete.\n");
}

module.exports = globalTeardown;
