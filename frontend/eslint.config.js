import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

// Lint del frontend. Objetivo: reglas de hooks de React + higiene de TS,
// sin ruido. Alineado con el criterio de backend/ruff.toml.
export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      // los objetos crudos de CyberArk no tienen tipo; se navegan con cuidado
      "@typescript-eslint/no-explicit-any": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  // primitivas shadcn/ui: el patrón cva exporta variantes junto al componente
  // (buttonVariants, badgeVariants). Es idiomático y el código es propio pero
  // "de librería" — no aplica la regla de fast-refresh.
  {
    files: ["src/components/ui/**/*.{ts,tsx}"],
    rules: { "react-refresh/only-export-components": "off" },
  },
  // config de tooling (Node, no browser)
  {
    files: ["*.config.{js,ts}", "vite.config.ts"],
    languageOptions: { globals: globals.node },
  },
);
