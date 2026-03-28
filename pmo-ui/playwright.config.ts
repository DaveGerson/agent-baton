/// <reference types="node" />
import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for the Baton PMO UI.
 *
 * Base URL: the Python backend serves the built app at /pmo/.
 * In CI / local runs without the backend, the Vite dev server is started
 * as a webServer so tests can still exercise the frontend against mocked
 * API responses injected via route interception in the test fixtures.
 */

export default defineConfig({
  testDir: './e2e/tests',

  // Maximum time one test can run.
  timeout: 30_000,

  // Fail the build on CI if you accidentally left test.only.
  forbidOnly: !!process.env.CI,

  // Retry once on CI to smooth over transient flakiness.
  retries: process.env.CI ? 1 : 0,

  // Parallelise across workers; keep sequential inside a single spec file.
  fullyParallel: true,
  workers: process.env.CI ? 2 : undefined,

  // Reporters: human-readable HTML report + machine-readable JSON.
  reporter: [
    ['html', { outputFolder: 'e2e/reports/html', open: 'never' }],
    ['json', { outputFile: 'e2e/reports/results.json' }],
    ['list'],
  ],

  // Shared settings for all projects.
  use: {
    // The backend serves the app at this base URL.
    // Set PLAYWRIGHT_BASE_URL (or BASE_URL) to override when running against the
    // Vite dev server:  PLAYWRIGHT_BASE_URL=http://localhost:3000/pmo/ npx playwright test
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? process.env.BASE_URL ?? 'http://localhost:8741/pmo/',

    // Screenshot on every failure.
    // Failed-test screenshots are written to the test output directory.
    // Manual captureFullPage() calls in tests write to e2e/screenshots/.
    screenshot: 'only-on-failure',

    // Short trace on first retry so failures are diagnosable.
    trace: 'on-first-retry',

    // Video on first retry for hard-to-reproduce failures.
    video: 'on-first-retry',

    // Sensible navigation timeout (separate from test timeout).
    navigationTimeout: 15_000,
    actionTimeout: 10_000,
  },

  // Only Chromium — faster feedback loop, sufficient for this app.
  projects: [
    {
      name: 'desktop',
      use: {
        ...devices['Desktop Chrome'],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: 'tablet',
      use: {
        ...devices['iPad (gen 7)'],
        viewport: { width: 768, height: 1024 },
      },
    },
    {
      name: 'mobile',
      use: {
        ...devices['iPhone 13'],
        viewport: { width: 375, height: 812 },
      },
    },
  ],

  // Start the Vite dev server if the production backend is not running.
  // Tests that need real API responses must either:
  //   (a) run against the live backend (default in CI/local with server up), or
  //   (b) mock API routes in the test fixture via page.route().
  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:3000/pmo/',
    reuseExistingServer: true,
    timeout: 30_000,
    // Use the Vite dev server URL when the Python backend is not available.
    // Override BASE_URL env var to switch between them.
    env: {
      VITE_API_BASE: process.env.VITE_API_BASE ?? '/api/v1/pmo',
    },
  },

  // Suppress the webServer if the production backend is explicitly targeted.
  // Set PMO_BACKEND_RUNNING=1 to skip starting the dev server.
  ...(process.env.PMO_BACKEND_RUNNING
    ? { webServer: undefined }
    : {}),
});
