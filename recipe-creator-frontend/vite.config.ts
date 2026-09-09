import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { svelteTesting } from '@testing-library/svelte/vite';
const apiPort = Number(process.env.RECIPE_E2E_API_PORT || 2332);
export default defineConfig({ plugins: [svelte(), svelteTesting()], server: { host: '127.0.0.1', port: 2772, strictPort: true, proxy: { '/api': `http://127.0.0.1:${apiPort}` } }, preview: { host: '127.0.0.1', port: 2772, strictPort: true }, test: { environment: 'jsdom', include: ['src/**/*.test.ts'], setupFiles: ['src/test-setup.ts'] } });
