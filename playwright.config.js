const { defineConfig, devices } = require("@playwright/test");
const fs = require("fs");
const path = require("path");
const { stripEnvValue } = require("./tests/frontend/playwright/test-utils");

function loadEnvFile() {
  const envPath = path.resolve(__dirname, ".env");
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
  return env;
}

function isFeatureEnabled(envVars, featureName) {
  const envVar = `${featureName.toUpperCase()}_ENABLED`;
  const value = (envVars[envVar] || "true").toLowerCase();
  return ["true", "1", "t", "yes"].includes(value);
}

const envVars = loadEnvFile();
const dashboardEnabled = isFeatureEnabled(envVars, "DASHBOARD");
const isCI = Boolean(process.env.CI);
// Set by scripts/validate_code.py — same tests as e2e:fast, with CI-like stability.
const isValidate = process.env.PLAYWRIGHT_VALIDATE === "1";
// Optional: firefox visual baselines only when explicitly requested (npm run test:frontend:e2e:firefox).
const includeFirefox = process.env.PLAYWRIGHT_INCLUDE_FIREFOX === "1";
const stableWorkers = isCI || isValidate;

const testMatch = ["tests/frontend/playwright/**/*.spec.js"];
const testIgnore = [];

if (!dashboardEnabled) {
  testIgnore.push("**/collab/dashboard/**");
}

/** @type {import('@playwright/test').Project[]} */
const projects = [
  // Fast path: mocked UI + API contract (no browser for contract file).
  {
    name: "chromium",
    grepInvert: /@live/,
    use: { ...devices["Desktop Chrome"] },
  },
  // Network smoke — run in CI and via npm run test:frontend:e2e:live.
  {
    name: "chromium-live",
    grep: /@live/,
    timeout: 90_000,
    retries: isCI ? 1 : 0,
    use: { ...devices["Desktop Chrome"] },
  },
];

if (includeFirefox) {
  projects.push({
    name: "firefox",
    grepInvert: /@live/,
    use: { ...devices["Desktop Firefox"] },
  });
}

module.exports = defineConfig({
  testDir: ".",
  testMatch,
  testIgnore,

  globalSetup: require.resolve("./tests/frontend/playwright/e2e-test-setup.js"),
  globalTeardown:
    require.resolve("./tests/frontend/playwright/e2e-test-teardown.js"),

  timeout: 30_000,
  expect: {
    timeout: 10_000,
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.05,
      threshold: 0.2,
      animations: "disabled",
    },
  },
  snapshotPathTemplate:
    "{testDir}/{testFileDir}/{testFileName}-snapshots/{arg}-{projectName}{ext}",

  // Mock suites use isolated contexts — safe to parallelize locally and in CI.
  fullyParallel: true,
  workers: stableWorkers ? 2 : undefined,
  retries: stableWorkers ? 1 : 0,

  reporter: isCI
    ? [["html", { outputFolder: "playwright-report", open: "never" }], ["list"]]
    : [["list"]],

  use: {
    baseURL: "http://127.0.0.1:8000",
    timezoneId: "UTC",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    trace: "retain-on-failure",
    navigationTimeout: 15_000,
    actionTimeout: 10_000,
  },

  projects,

  webServer: {
    command:
      "node tests/frontend/playwright/pre-test-cleanup.js && " +
      "python -m http.server 8000 --directory collab/dashboard",
    env: {
      E2E_TEST: "true",
      DASHBOARD_ENABLED: dashboardEnabled ? "true" : "false",
    },
    url: "http://127.0.0.1:8000",
    reuseExistingServer: !isCI,
    timeout: 30_000,
    stdout: "pipe",
    stderr: "pipe",
  },
});
