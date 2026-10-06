/**
 * End-to-end tests: the built web app in a real browser against the mock agent.
 *
 * Run `npm run build` first; Playwright then starts the mock (port 8010) and the app (port
 * 3010) itself. PLAYWRIGHT_CHROMIUM_EXECUTABLE points to a Chromium that is already installed;
 * without it, Playwright uses its own (`npx playwright install chromium`).
 *
 * E2E_BASE_URL runs the tests against an app that is already running instead, for example the
 * containers from compose.mock.yaml (E2E_BASE_URL=http://localhost:3000).
 */
import { defineConfig, devices } from "@playwright/test";

const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE;
const externalBaseUrl = process.env.E2E_BASE_URL;

export default defineConfig({
  testDir: "e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: externalBaseUrl ?? "http://localhost:3010",
    locale: "sv-SE",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: {
        ...devices["Desktop Chrome"],
        launchOptions: executablePath ? { executablePath } : {},
      },
    },
  ],
  webServer: externalBaseUrl
    ? undefined
    : [
        {
          command: "node mock/server.ts",
          url: "http://localhost:8010/agui/health",
          env: { MOCK_PORT: "8010", MOCK_FAST: "1" },
          reuseExistingServer: !process.env.CI,
        },
        {
          command: "npm run start",
          url: "http://localhost:3010",
          env: {
            PORT: "3010",
            HOSTNAME: "localhost",
            API_URL: "http://localhost:8010",
            COPILOTKIT_TELEMETRY_DISABLED: "true",
          },
          reuseExistingServer: !process.env.CI,
        },
      ],
});
