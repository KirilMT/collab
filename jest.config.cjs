/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: "node",
  testMatch: ["<rootDir>/tests/frontend/unit/**/*.test.js"],
  collectCoverageFrom: [
    "collab/dashboard/dashboard-format.js",
    "collab/dashboard/dashboard-filters.js",
    "collab/dashboard/dashboard-charts.js",
  ],
  // Frontend coverage floor, aligned with the project-wide 95% standard
  // (AGENTS.md; codecov.yml `patch.target: 95%`). New-code/diff coverage is
  // enforced per-PR by codecov's patch status; this global floor guards against
  // regressions in the dashboard JS as a whole. Branches sit just under the
  // others because the UMD wrapper boilerplate in each module
  // (`typeof globalThis !== "undefined" ? globalThis : this` and the
  // browser-global `else` arm that never runs under `require`) is structurally
  // unreachable in the Node/Jest environment.
  coverageThreshold: {
    global: {
      branches: 95,
      functions: 95,
      lines: 95,
      statements: 95,
    },
  },
};
