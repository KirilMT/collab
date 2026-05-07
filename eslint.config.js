const globals = require("globals");
const js = require("@eslint/js");

module.exports = [
  js.configs.recommended,
  {
    ignores: ["node_modules/**", ".venv/**", "htmlcov/**", "coverage/**"],
  },
  {
    // src/dashboard/**/*.js is included here for when JS files are extracted from index.html
    files: ["src/dashboard/**/*.js", "tests/frontend/playwright/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        ...globals.browser,
        ...globals.jest,
        ...globals.node,
      },
    },
    rules: {
      "no-unused-vars": "warn",
      "no-console": ["warn", { allow: ["warn", "error"] }],
      "no-undef": "error",
    },
  },
  {
    // VS Code extension — Node.js runtime, allow unused variables prefixed with underscore
    files: ["vscode-extension/collab-locks/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "commonjs",
      globals: globals.node,
    },
    rules: {
      "no-unused-vars": ["error", { argsIgnorePattern: "^_|^e$", varsIgnorePattern: "^_|^e$" }],
    },
  },
];
