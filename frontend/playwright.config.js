import { defineConfig, devices } from "@playwright/test";

process.env.PLAYWRIGHT_PORT ||= String(40_000 + Math.floor(Math.random() * 20_000));
const port = process.env.PLAYWRIGHT_PORT;
const baseURL = `http://127.0.0.1:${port}`;
const python = ".venv\\Scripts\\python.exe";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 90_000,
  expect: { timeout: 10_000 },
  reporter: "list",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "chrome", use: { ...devices["Desktop Chrome"], channel: "chrome" } },
    { name: "firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "edge", use: { ...devices["Desktop Edge"], channel: "msedge" } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
    { name: "mobile-chromium", use: { ...devices["iPhone 13"], browserName: "chromium" } },
  ],
  webServer: {
    command: `${python} scripts\\playwright_server.py`,
    cwd: "..",
    url: `${baseURL}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { PLAYWRIGHT_PORT: port },
  },
});
