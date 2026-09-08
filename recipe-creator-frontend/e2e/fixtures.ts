import { test as base, expect, type APIRequestContext, type APIResponse, type BrowserContext, type Page } from '@playwright/test';
import type { Session, Recipe } from '../src/types';
import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';

export const origin = `http://127.0.0.1:${Number(process.env.RECIPE_E2E_PORT || 2772)}`;
export const source = '  A family recipe.\n\n½ cup stock\n\nHeat to 180°C.\n  Wait 20 minutes.\n\nNotes: never rewrite this.  \n';
export async function session(api: APIRequestContext): Promise<Session> {
  const result = await api.get('/api/session');
  expect(result.ok(), 'Start the actual API on port 2332 with an isolated database and the Vite origin allowed').toBeTruthy();
  return result.json();
}
export async function mutate(api: APIRequestContext, path: string, data?: unknown, method = 'POST'): Promise<APIResponse> {
  const identity = await session(api);
  return api.fetch(`/api${path}`, {method, data, headers: {'Origin': origin, 'X-CSRF-Token': identity.csrf_token}});
}
export async function createProfile(api: APIRequestContext, display_name: string) {
  const result = await mutate(api, '/identity', {display_name});
  expect(result.status()).toBe(201);
  return session(api);
}
export async function createRecipe(api: APIRequestContext): Promise<Recipe> {
  const result = await mutate(api, '/recipes', {title: `Browser recipe ${crypto.randomUUID()}`, mode: 'text', source_text: source});
  expect(result.status()).toBe(201);
  return result.json();
}

type Fixtures = { second: {context: BrowserContext; page: Page}; owner: Session; recipe: Recipe; adminPermission: (userId: string, granted: boolean) => Promise<void>; apiReady: void };
export const test = base.extend<Fixtures>({
  apiReady: [async ({context}, use) => {await session(context.request); await use();}, {auto: true}],
  second: async ({browser}, use) => {
    const context = await browser.newContext({baseURL: origin});
    await use({context, page: await context.newPage()});
    await context.close();
  },
  owner: async ({context}, use) => {await use(await createProfile(context.request, `Cook ${crypto.randomUUID().slice(0, 8)}`));},
  recipe: async ({context, owner}, use) => {
    expect(owner.user).not.toBeNull();
    const recipe = await createRecipe(context.request);
    await use(recipe);
    const current = await context.request.get(`/api/recipes/${encodeURIComponent(recipe.id)}`);
    if (current.ok()) await mutate(context.request, `/recipes/${encodeURIComponent(recipe.id)}?expected_revision=${(await current.json()).revision}`, undefined, 'DELETE');
  },
  adminPermission: async ({}, use) => {
    const granted = new Set<string>();
    const change = async (userId: string, enabled: boolean) => {
      const {stdout} = await promisify(execFile)('python3', [fileURLToPath(new URL('./start-backend.py', import.meta.url)),
        enabled ? 'admin-grant' : 'admin-revoke', '--user-id', userId, '--yes']);
      expect(JSON.parse(stdout)).toMatchObject({id: userId, is_admin: enabled});
      if (enabled) granted.add(userId); else granted.delete(userId);
    };
    try {await use(change);} finally {for (const userId of granted) await change(userId, false);}
  },
});
export { expect };
