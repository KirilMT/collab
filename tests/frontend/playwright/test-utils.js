/**
 * Shared test utilities for E2E tests.
 *
 * Follows the same pattern as backend tests (conftest.py) for feature toggling
 * via .env configuration.
 */

const fs = require("fs");
const path = require("path");

const PROJECT_ROOT = path.resolve(__dirname, "../../..");

/**
 * Load environment variables from .env file.
 */
function loadEnvFile() {
  const envPath = path.resolve(PROJECT_ROOT, ".env");
  const env = {};
  if (fs.existsSync(envPath)) {
    const content = fs.readFileSync(envPath, "utf8");
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
 * Check if a feature is enabled based on .env configuration.
 * Follows the same logic as backend tests: defaults to true unless explicitly
 * disabled.
 *
 * @param {string} featureName - The feature name (e.g., 'DASHBOARD', 'DAEMON')
 * @returns {boolean} - Whether the feature is enabled
 */
function isFeatureEnabled(featureName) {
  const envVars = loadEnvFile();
  const envVar = `${featureName.toUpperCase()}_ENABLED`;
  const value = (envVars[envVar] || "true").toLowerCase();
  return ["true", "1", "t", "yes"].includes(value);
}

/**
 * Check if Supabase credentials are configured in .env.
 * Live-lock features in the dashboard require valid credentials.
 *
 * @returns {boolean} - Whether credentials are present
 */
function hasSupabaseCredentials() {
  const envVars = loadEnvFile();
  return Boolean(envVars.SUPABASE_URL && envVars.SUPABASE_ANON_KEY);
}

/**
 * Skip all tests in a describe block if a feature is disabled.
 * Use this at the top of feature-gated test files.
 *
 * @param {import('@playwright/test').test} test - The Playwright test object
 * @param {string} featureName - The feature name (e.g., 'DASHBOARD', 'DAEMON')
 * @returns {boolean} - Whether tests should run
 */
function skipIfFeatureDisabled(test, featureName) {
  const enabled = isFeatureEnabled(featureName);
  if (!enabled) {
    // eslint-disable-next-line no-console
    console.log(`Skipping ${featureName} tests — feature disabled in .env`);
    test.skip(true, `${featureName} feature is disabled in .env`);
  }
  return enabled;
}

/**
 * Skip tests that require live Supabase connection when credentials are absent.
 *
 * @param {import('@playwright/test').test} test - The Playwright test object
 * @returns {boolean} - Whether live tests should run
 */
function skipIfNoSupabaseCredentials(test) {
  const hasCredentials = hasSupabaseCredentials();
  if (!hasCredentials) {
    // eslint-disable-next-line no-console
    console.log("Skipping live Supabase tests — no credentials in .env");
    test.skip(
      true,
      "Supabase credentials not configured. Copy .env.example to .env.",
    );
  }
  return hasCredentials;
}

module.exports = {
  loadEnvFile,
  isFeatureEnabled,
  hasSupabaseCredentials,
  skipIfFeatureDisabled,
  skipIfNoSupabaseCredentials,
};
