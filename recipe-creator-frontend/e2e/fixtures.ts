import { test as base, expect, type APIRequestContext, type APIResponse, type BrowserContext, type Page } from '@playwright/test';
import type { Session, Recipe } from '../src/types';

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

type Fixtures = { second: {context: BrowserContext; page: Page}; owner: Session; recipe: Recipe; adminPassword: string; apiReady: void };
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
  adminPassword: async ({}, use) => {
    const password = process.env.RECIPE_E2E_ADMIN_PASSWORD;
    expect(password, 'Set RECIPE_E2E_ADMIN_PASSWORD to the isolated API admin password').toBeTruthy();
    await use(password!);
  },
});
export { expect };
