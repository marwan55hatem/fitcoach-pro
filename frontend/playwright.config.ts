import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against the real API (port 8001, your local database) and the web
 * app (port 3001). Run `docker compose up -d` and seed the exercise library first.
 * Running servers are reused; otherwise both are started here.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1, // the API's auth rate limit is shared by every test
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
      reuseExistingServer: true,
      timeout: 60_000,
    },
    {
      command: "npm run dev",
      url: "http://localhost:3001/en/login",
      reuseExistingServer: true,
      timeout: 120_000,
    },
  ],
});
