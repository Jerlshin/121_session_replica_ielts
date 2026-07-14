import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

export default defineConfig({
  // The app itself relies on Next.js's SWC compiler for JSX; vitest's
  // esbuild-based transform needs this told explicitly, or every .tsx test
  // file fails with "React is not defined" under React 18's automatic
  // runtime (no next/babel plugin involved here at all).
  esbuild: {
    jsx: "automatic",
  },
  resolve: {
    alias: {
      "@": path.resolve(path.dirname(fileURLToPath(import.meta.url)), "src"),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    globals: true,
  },
});
