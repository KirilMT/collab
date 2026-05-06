/* eslint-disable no-console */
/**
 * Pre-Test Cleanup
 *
 * Runs before the Playwright webServer starts. Ensures:
 * 1. Any stale process on the dashboard port is killed
 * 2. Any stale collab daemon from a previous test run is stopped
 * 3. No leftover E2E test lock files block the test run
 *
 * Called from playwright.config.js webServer command before starting the server.
 */

const { execSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const TEST_PORT = 8000;
const MAX_RETRY_ATTEMPTS = 5;
const RETRY_DELAY_MS = 500;

// Script location: tests/frontend/playwright/pre-test-cleanup.js
// Root is ../../..
const PROJECT_ROOT = path.resolve(__dirname, "../../..");
const COLLAB_DIR = path.join(PROJECT_ROOT, ".collab");

/**
 * Sleep for specified milliseconds.
 */
function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Kill any running collab daemon processes that might be holding port or lock files.
 */
function killCollabDaemonProcesses() {
  console.log("🔪 Stopping any stale collab daemon processes...");

  try {
    // Kill Python processes running collab daemon
    execSync(
      "wmic process where \"Name='python.exe' and CommandLine like '%collab%daemon%'\" call terminate",
      { stdio: "ignore" },
    );
  } catch (_e) {
    // No process found — this is fine
  }

  try {
    // Catch processes started from this project folder
    execSync(
      `wmic process where "CommandLine like '%collab%' and Name='python.exe'" call terminate`,
      { stdio: "ignore" },
    );
  } catch (_e) {
    // Ignore
  }
}

/**
 * Kill any process listening on the given port.
 */
function killPort(port) {
  try {
    const output = execSync(`netstat -ano | findstr :${port}`).toString();
    const lines = output.trim().split("\n");
    const killedPids = new Set();

    for (const line of lines) {
      const parts = line.trim().split(/\s+/);
      const pid = parts[parts.length - 1]; // PID is the last column

      if (pid && /^\d+$/.test(pid) && pid !== "0" && !killedPids.has(pid)) {
        try {
          console.warn(
            `🔪 Killing stale process on port ${port} (PID: ${pid})...`,
          );
          execSync(`taskkill /F /PID ${pid}`, { stdio: "ignore" });
          killedPids.add(pid);
        } catch (killErr) {
          console.warn(`⚠️ Could not kill PID ${pid}: ${killErr.message}`);
        }
      }
    }

    return killedPids.size > 0;
  } catch (_e) {
    // Port not in use — this is fine
  }
  return false;
}

/**
 * Remove a stale lock file with retry logic.
 * Handles cases where a file is still locked by a recently-killed process.
 */
async function deleteLockWithRetry(lockPath) {
  if (!fs.existsSync(lockPath)) {
    return true;
  }

  for (let attempt = 1; attempt <= MAX_RETRY_ATTEMPTS; attempt++) {
    try {
      fs.unlinkSync(lockPath);
      console.log(`✅ Deleted stale lock: ${path.basename(lockPath)}`);
      return true;
    } catch (e) {
      if (attempt < MAX_RETRY_ATTEMPTS) {
        console.warn(
          `⚠️ Attempt ${attempt}/${MAX_RETRY_ATTEMPTS} failed to delete ` +
            `${path.basename(lockPath)}: ${e.code || e.message}`,
        );
        if (global.gc) {
          global.gc();
        }
        await sleep(RETRY_DELAY_MS * attempt);
      } else {
        console.error(
          `❌ Failed to delete ${lockPath} after ${MAX_RETRY_ATTEMPTS} attempts.`,
        );
        return false;
      }
    }
  }
  return false;
}

/**
 * Remove E2E test lock artifacts left in .collab/ from a previous test run.
 */
async function cleanupCollabTestLocks() {
  if (!fs.existsSync(COLLAB_DIR)) {
    return;
  }

  let cleaned = 0;
  try {
    const entries = fs.readdirSync(COLLAB_DIR);
    for (const entry of entries) {
      if (!entry.endsWith(".lock")) {
        continue;
      }
      const entryPath = path.join(COLLAB_DIR, entry);
      try {
        const content = fs.readFileSync(entryPath, "utf8");
        if (content.includes("e2e_test") || content.includes("playwright")) {
          await deleteLockWithRetry(entryPath);
          cleaned++;
        }
      } catch (_err) {
        // Skip unreadable files
      }
    }
  } catch (error) {
    console.warn(`⚠️ Could not scan .collab/ directory: ${error.message}`);
  }

  if (cleaned === 0) {
    console.log("   No stale E2E test lock files found.");
  }
}

// Main execution
async function main() {
  console.log("🧹 Running pre-test cleanup...");

  // Step 1: Kill any process on the dashboard port
  const killedPort = killPort(TEST_PORT);

  // Step 2: Kill any stale collab daemon processes
  killCollabDaemonProcesses();

  // Step 3: Wait if we killed processes so locks release
  if (killedPort) {
    console.log("⏳ Waiting for process termination...");
    await sleep(1000);
  }

  // Step 4: Remove stale E2E test lock artifacts
  await cleanupCollabTestLocks();

  console.log("✅ Pre-test cleanup complete.\n");
}

main().catch((err) => {
  console.error("❌ Pre-test cleanup failed:", err.message);
  process.exit(1);
});
