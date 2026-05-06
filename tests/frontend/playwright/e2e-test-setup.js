/**
 * E2E Test Global Setup
 *
 * This script runs BEFORE any Playwright tests. It ensures:
 * 1. No conflicting process is running on the dashboard test port
 * 2. Supabase credentials are present in .env (warned if missing)
 * 3. Environment is ready for E2E testing of the collab dashboard
 *
 * Usage: Configured in playwright.config.js as globalSetup
 */

const path = require("path");
const fs = require("fs");

// Configuration
const TEST_PORT = 8000;
const TEST_HOST = "127.0.0.1";
const PROJECT_ROOT = path.resolve(__dirname, "../../..");
const ENV_PATH = path.join(PROJECT_ROOT, ".env");

/**
 * Load environment variables from .env file.
 * Follows the same pattern as backend tests (conftest.py).
 */
function loadEnvFile() {
  const env = {};
  if (fs.existsSync(ENV_PATH)) {
    const content = fs.readFileSync(ENV_PATH, "utf8");
    for (const line of content.split("\n")) {
      const trimmed = line.trim();
      if (trimmed && !trimmed.startsWith("#")) {
        const [key, ...valueParts] = trimmed.split("=");
        if (key && valueParts.length > 0) {
          env[key.trim()] = valueParts.join("=").trim();
        }
      }
    }
  }
  return env;
}

/**
 * Global setup function called by Playwright
 *
 * NOTE: Playwright's webServer config starts the static file server BEFORE this
 * runs. We should NOT kill any servers here — just validate the environment.
 */
async function globalSetup() {
  console.warn("\n🚀 E2E Test Global Setup Starting...\n");

  const envVars = loadEnvFile();

  // Warn if Supabase credentials are missing — dashboard JS may be non-functional
  // but structural/visual tests can still run without them.
  if (!envVars.SUPABASE_URL || !envVars.SUPABASE_ANON_KEY) {
    console.warn(
      "⚠️  Supabase credentials not found in .env — dashboard live-lock " +
        "features will not be functional during E2E tests.",
    );
    console.warn(
      "   Copy .env.example to .env and fill credentials to enable full tests.",
    );
  } else {
    console.warn("✅ Supabase credentials detected.");
  }

  console.warn(`📝 Dashboard will be served on: ${TEST_HOST}:${TEST_PORT}`);
  console.warn(
    `   Source: ${path.join(PROJECT_ROOT, "src", "dashboard", "index.html")}\n`,
  );

  console.warn(
    "✅ Global setup complete. Playwright will start the dashboard server.\n",
  );
}

module.exports = globalSetup;

// Export constants for use in other test files
module.exports.TEST_PORT = TEST_PORT;
module.exports.TEST_HOST = TEST_HOST;
module.exports.PROJECT_ROOT = PROJECT_ROOT;
