import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  outputDir: "./test-results",
  fullyParallel: false,
  use: { baseURL: "http://127.0.0.1:3324", screenshot: "only-on-failure" },
  webServer: {
    command: `../../node_modules/.bin/next ${process.env.CI ? "start" : "dev"} -p 3324 --hostname 127.0.0.1`,
    url: "http://127.0.0.1:3324",
    env: { TOUR_BACKEND_MODE: "warehouse" },
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
