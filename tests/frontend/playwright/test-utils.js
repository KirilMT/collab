/**
 * Shared test utilities for E2E tests.
 *
 * Follows the same pattern as backend tests (conftest.py) for feature toggling
 * via .env configuration.
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

const PROJECT_ROOT = path.resolve(__dirname, "../../..");
const PLAYWRIGHT_LIVE_DASHBOARD = path.join(
  PROJECT_ROOT,
  "collab",
  "dashboard",
  "playwright-live.html",
);

/**
 * Strip surrounding quotes and whitespace from .env values (matches python-dotenv).
 *
 * @param {string} value
 * @returns {string}
 */
function stripEnvValue(value) {
  let v = String(value || "").trim();
  // Inline comments (python-dotenv behaviour), e.g. KEY=xxx  # note
  const commentIdx = v.search(/\s+#/);
  if (commentIdx !== -1) {
    v = v.slice(0, commentIdx).trim();
  }
  if (
    (v.startsWith('"') && v.endsWith('"')) ||
    (v.startsWith("'") && v.endsWith("'"))
  ) {
    v = v.slice(1, -1);
  }
  return v;
}

/**
 * Load environment variables from .env file.
 */
const ENV_KEYS_FROM_PROCESS = [
  "SUPABASE_URL",
  "SUPABASE_ANON_KEY",
  "SUPABASE_SERVICE_ROLE_KEY",
  "COLLAB_DEVELOPER_ID",
  "DEVELOPER_ID",
  "GITHUB_USER",
];

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
          env[key.trim()] = stripEnvValue(valueParts.join("="));
        }
      }
    }
  }
  for (const key of ENV_KEYS_FROM_PROCESS) {
    if (process.env[key]) {
      env[key] = stripEnvValue(process.env[key]);
    }
  }
  return env;
}

/**
 * Headers for direct PostgREST checks (schema contract / smoke).
 *
 * @param {ReturnType<typeof buildSupabaseConfigFromEnv>} config
 * @returns {{ apikey: string, Authorization: string, Accept: string }}
 */
function supabaseRestHeaders(config) {
  const key = config.serviceKey || config.anonKey;
  return {
    apikey: key,
    Authorization: `Bearer ${key}`,
    Accept: "application/json",
  };
}

/**
 * Resolve developer identity the same way as collab.lock_client.LockClient.
 *
 * @param {Record<string, string>} envVars
 * @returns {string}
 */
function resolveDeveloperId(envVars) {
  if (envVars.COLLAB_DEVELOPER_ID) {
    return envVars.COLLAB_DEVELOPER_ID;
  }
  try {
    const name = execSync("git config user.name", {
      encoding: "utf8",
      cwd: PROJECT_ROOT,
      stdio: ["ignore", "pipe", "ignore"],
    }).trim();
    if (name) {
      return name;
    }
  } catch {
    // git not available or user.name unset
  }
  return (
    envVars.USERNAME ||
    envVars.USER ||
    process.env.USERNAME ||
    process.env.USER ||
    "unknown_user"
  );
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
 * Build dashboard `window.__SUPABASE_CONFIG__` from .env (same shape as
 * `collab dashboard` / lock_client injection). Used for optional live smoke tests.
 *
 * @returns {{ url: string, anonKey: string, serviceKey: string|null, user: string }}
 */
function buildSupabaseConfigFromEnv() {
  const envVars = loadEnvFile();
  const serviceKey = envVars.SUPABASE_SERVICE_ROLE_KEY || "";
  return {
    url: envVars.SUPABASE_URL || "",
    anonKey: envVars.SUPABASE_ANON_KEY || "",
    serviceKey: serviceKey || null,
    user: resolveDeveloperId(envVars),
  };
}

/**
 * Write a dashboard HTML file with injected Supabase config prepended, matching
 * `collab dashboard` / lock_client._prepare_dashboard_server() exactly.
 *
 * @returns {boolean} true when the live dashboard file was written
 */
function writePlaywrightLiveDashboardHtml() {
  const config = buildSupabaseConfigFromEnv();
  if (!config.url || !config.anonKey) {
    return false;
  }

  const htmlPath = path.join(PROJECT_ROOT, "collab", "dashboard", "index.html");
  const content = fs.readFileSync(htmlPath, "utf8");
  const injectScript = `<script>window.__SUPABASE_CONFIG__ = ${JSON.stringify(config)};</script>\n`;
  fs.writeFileSync(PLAYWRIGHT_LIVE_DASHBOARD, injectScript + content, "utf8");
  return true;
}

/**
 * @returns {boolean}
 */
function playwrightLiveDashboardExists() {
  return fs.existsSync(PLAYWRIGHT_LIVE_DASHBOARD);
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
  stripEnvValue,
  isFeatureEnabled,
  hasSupabaseCredentials,
  buildSupabaseConfigFromEnv,
  supabaseRestHeaders,
  resolveDeveloperId,
  writePlaywrightLiveDashboardHtml,
  playwrightLiveDashboardExists,
  PLAYWRIGHT_LIVE_DASHBOARD,
  skipIfFeatureDisabled,
  skipIfNoSupabaseCredentials,
};
