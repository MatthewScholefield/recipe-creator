import { defineConfig } from '@playwright/test';

const port = Number(process.env.RECIPE_E2E_PORT || 2772);
const origin = `http://127.0.0.1:${port}`;
process.env.RECIPE_E2E_ADMIN_PASSWORD ||= 'recipe-e2e-test-only';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: { baseURL: origin, trace: 'retain-on-failure' },
  webServer: [
    {
      url: 'http://127.0.0.1:2332/api/session',
      reuseExistingServer: false,
      gracefulShutdown: { signal: 'SIGTERM', timeout: 10_000 },
      timeout: 120_000,
    },
    {
      command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${port} --strictPort`,
      url: origin,
      reuseExistingServer: false,
    },
  ],
});
