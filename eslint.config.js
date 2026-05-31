const globals = require("globals");
const js = require("@eslint/js");

module.exports = [
  js.configs.recommended,
  {
    ignores: ["node_modules/**", ".venv/**", "htmlcov/**", "coverage/**"],
  },
  {
    files: ["playwright.config.js", "jest.config.cjs", "eslint.config.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "commonjs",
      globals: globals.node,
    },
    rules: {
      "no-unused-vars": "warn",
      "no-console": ["warn", { allow: ["warn", "error"] }],
      "no-undef": "error",
    },
  },
  {
    // collab/dashboard/**/*.js is included here for when JS files are extracted from index.html
    files: ["collab/dashboard/**/*.js", "tests/frontend/playwright/**/*.js"],
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
      "no-unused-vars": [
        "warn",
        {
          argsIgnorePattern: "^_",
          varsIgnorePattern: "^_",
          caughtErrorsIgnorePattern: "^_",
        },
      ],
      "no-console": ["warn", { allow: ["warn", "error"] }],
      "no-undef": "error",
    },
  },
  {
    files: ["tests/frontend/unit/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "commonjs",
      globals: {
        ...globals.node,
        ...globals.jest,
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
    files: ["editors/vscode/collab-locks/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "commonjs",
      globals: globals.node,
    },
    rules: {
      "no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_|^e$", varsIgnorePattern: "^_|^e$" },
      ],
    },
  },
];
