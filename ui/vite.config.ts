import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The dev server proxies the API so the browser talks to one origin (no CORS in the API).
// Default target is the stub API (`make stub-api`, port 8000); point API_TARGET at the real Brain when it exists.
const target = process.env.API_TARGET ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/v1": target, "/sim": target },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"], // e2e/ is Playwright (npm run e2e)
  },
});
