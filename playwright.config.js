const { defineConfig, devices } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

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
          env[key.trim()] = valueParts.join("=").trim();
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

const testMatch = ["tests/frontend/playwright/**/*.spec.js"];
const testIgnore = [];

if (!dashboardEnabled) {
  testIgnore.push("**/collab/dashboard/**");
}

module.exports = defineConfig({
  testDir: ".",
  testMatch,
  testIgnore,

  // Global setup runs before all tests
  globalSetup: require.resolve(
    "./tests/frontend/playwright/e2e-test-setup.js",
  ),

  // Global teardown runs after all tests (cleanup like pytest)
  globalTeardown: require.resolve(
    "./tests/frontend/playwright/e2e-test-teardown.js",
  ),
  timeout: 30000,
  expect: {
    timeout: 10000,
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.05,
      threshold: 0.2,
      animations: "disabled",
    },
  },
  snapshotPathTemplate:
    "{testDir}/{testFileDir}/{testFileName}-snapshots/{arg}-{projectName}{ext}",
  retries: process.env.CI ? 2 : 1,
  fullyParallel: false,
  workers: 1,
  reporter: [
    ["html", { outputFolder: "playwright-report", open: "never" }],
    ["list"],
  ],
  use: {
    baseURL: "http://127.0.0.1:8000",
    timezoneId: "UTC",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    trace: "retain-on-failure",
    navigationTimeout: 15000,
    actionTimeout: 10000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "firefox",
      use: { ...devices["Desktop Firefox"] },
    },
  ],

  // Web server — serves the collab dashboard as a static site for E2E testing
  webServer: {
    command:
      "node tests/frontend/playwright/pre-test-cleanup.js && " +
      "python -m http.server 8000 --directory collab/dashboard",
    env: {
      E2E_TEST: "true",
      DASHBOARD_ENABLED: dashboardEnabled ? "true" : "false",
    },
    url: "http://127.0.0.1:8000",

    // Don't reuse existing server — always start fresh for isolation
    reuseExistingServer: !process.env.CI,

    // Give server time to start
    timeout: 30000,

    stdout: "pipe",
    stderr: "pipe",
  },
});
