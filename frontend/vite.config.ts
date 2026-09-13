/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * Production build config.
 *
 * `npm run build` emits `dist/`, which the FastAPI backend serves automatically
 * in preference to the in-browser loader (see backend/app/api/frontend.py).
 * During `npm run dev` the API is proxied so the UI stays same-origin.
 */
export default defineConfig({
  // Classic JSX runtime keeps this build byte-compatible with the no-Node
  // browser loader, which compiles the same sources with Babel.
  plugins: [react({ jsxRuntime: "classic" })],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
