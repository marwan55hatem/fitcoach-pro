import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the real API (port 8001, your local database) and the web
 * app (port 3001). Run `docker compose up -d` and seed the exercise library first.
 * The API is always started here with the demo AI; a running web server is reused.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1, // one database and one API for every test
  retries: 0,
  reporter: [["list"]],
  use: {
    baseURL: "http://localhost:3001",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "mobile", use: { ...devices["Pixel 7"] } }],
  webServer: [
    {
      command: "..\\backend\\.venv\\Scripts\\python.exe -m uvicorn app.main:app --port 8001",
      cwd: "../backend",
      url: "http://localhost:8001/health",
      // Always a fresh API for the tests: the offline demo AI (never a real model or a
      // paid call) and a higher sign-in limit, since each test creates an account.
      // Stop your own API on port 8001 before running the tests.
      reuseExistingServer: false,
      timeout: 60_000,
      env: { RATE_LIMIT_AUTH: "1000/minute", AI_PROVIDER: "demo", APP_ENV: "development" },
    },
    {
      command: "npm run dev",
      url: "http://localhost:3001/en/login",
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
