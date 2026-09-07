import { defineConfig } from 'vitest/config';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import { svelteTesting } from '@testing-library/svelte/vite';
export default defineConfig({ plugins: [svelte(), svelteTesting()], server: { proxy: { '/api': 'http://127.0.0.1:8080' } }, test: { environment: 'jsdom', include: ['src/**/*.test.ts'], setupFiles: ['src/test-setup.ts'] } });
