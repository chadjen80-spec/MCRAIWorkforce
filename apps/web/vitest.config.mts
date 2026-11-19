import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Unit tests (src/**/*.test.ts, node) and component tests (src/**/*.test.tsx, jsdom per file).
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  oxc: {
    jsx: { runtime: "automatic" },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
