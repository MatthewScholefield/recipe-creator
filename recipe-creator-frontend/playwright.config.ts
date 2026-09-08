import { defineConfig } from '@playwright/test';

const port = Number(process.env.RECIPE_E2E_PORT || 2772);
const origin = `http://127.0.0.1:${port}`;
const mocked = process.env.RECIPE_E2E_MOCKED === '1';
process.env.RECIPE_E2E_ADMIN_PASSWORD ||= 'recipe-e2e-test-only';

export default defineConfig({
  testDir: './e2e',
  ...(mocked ? { testMatch: '**/browse*.spec.ts' } : { testIgnore: '**/browse*.spec.ts' }),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: { baseURL: origin, trace: 'retain-on-failure' },
  webServer: [
    ...(!mocked ? [{
      url: 'http://127.0.0.1:2332/api/session',
      reuseExistingServer: false,
      gracefulShutdown: { signal: 'SIGTERM' as const, timeout: 10_000 },
      timeout: 120_000,
    }] : []),
    {
      command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${port} --strictPort`,
      url: origin,
      reuseExistingServer: false,
    },
  ],
});
