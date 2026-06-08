/** @type {import('jest').Config} */
module.exports = {
  testEnvironment: "node",
  testMatch: ["<rootDir>/tests/frontend/unit/**/*.test.js"],
  collectCoverageFrom: [
    "collab/dashboard/dashboard-format.js",
    "collab/dashboard/dashboard-filters.js",
    "collab/dashboard/dashboard-charts.js",
  ],
  coverageThreshold: {
    global: {
      branches: 60,
      functions: 70,
      lines: 60,
      statements: 60,
    },
  },
};
