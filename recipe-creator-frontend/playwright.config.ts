import { defineConfig } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const port = Number(process.env.RECIPE_E2E_PORT || 2772);
const apiPort = Number(process.env.RECIPE_E2E_API_PORT || 2332);
const origin = `http://127.0.0.1:${port}`;
const mocked = process.env.RECIPE_E2E_MOCKED === '1';
if (!mocked) process.env.RECIPE_E2E_STATE ||= join(tmpdir(), `recipe-e2e-${randomUUID()}.json`);

export default defineConfig({
  testDir: './e2e',
  ...(mocked ? { testMatch: '**/browse*.spec.ts' } : { testIgnore: '**/browse*.spec.ts' }),
  fullyParallel: false,
  workers: 1,
  retries: 0,
  use: { baseURL: origin, trace: 'retain-on-failure' },
  webServer: [
    ...(!mocked ? [{
      command: 'python3 e2e/start-backend.py',
      url: `http://127.0.0.1:${apiPort}/api/session`,
      reuseExistingServer: false,
      gracefulShutdown: { signal: 'SIGTERM' as const, timeout: 10_000 },
      timeout: 120_000,
    }] : []),
    {
      command: `bun run build && bun run preview -- --host 127.0.0.1 --port ${port} --strictPort`,
      url: origin,
      reuseExistingServer: false,
    },
  ],
});
