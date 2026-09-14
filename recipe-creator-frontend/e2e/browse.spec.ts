import { test, expect, type Page, type Route } from '@playwright/test';
import { blank } from '../src/recipe';

const classifiers = ['breakfast', 'lunch', 'dinner', 'dessert'];
const catalog = ['american', 'asian', 'breakfast', 'dessert', 'dinner', 'indian', 'lunch', 'mexican', 'vegetarian'];
const copy = {site_title: 'Community kitchen', site_tagline: 'Food from our neighbors', home_title: '<b>Our recipes</b>', home_intro: 'Cook something together.', footer_text: 'Shared with care.'};
const summary = (id: string, tags: string[] = []) => ({id, title: `Recipe ${id}`, tags, description: '', thumbnail_photo_id: null, owner_id: null, author_name: null});
type Lookup = {ids: string[]; q?: string; tags: string[]};

async function mockApi(page: Page, handle?: (route: Route, url: URL) => Promise<boolean>) {
  const unexpected: string[] = [], errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (await handle?.(route, url)) return;
    const responses: Record<string, unknown> = {
      '/api/session': {user: null, device_id: null, admin: false, csrf_token: 'browser-test'},
      '/api/site-settings': {revision: 1, copy},
      '/api/tags': {tags: catalog, classifier_tags: classifiers},
      '/api/recipes': {items: [], has_more: false, errors: []},
    };
    if (!(url.pathname in responses)) {
      unexpected.push(`${route.request().method()} ${url.pathname}`);
      await route.fulfill({status: 500, json: {detail: 'Unexpected mocked request'}});
    } else await route.fulfill({json: responses[url.pathname]});
  });
  return () => {expect(unexpected).toEqual([]); expect(errors).toEqual([]);};
}

async function seedBookmarks(page: Page, values: unknown[]) {
  await page.addInitScript(values => localStorage.setItem('notebook:bookmarks', JSON.stringify(values)), values);
}

async function search(page: Page, value: string) {
  await page.getByRole('searchbox', {name: 'Search recipes', exact: true}).fill(value);
  await page.getByRole('button', {name: 'Search recipes', exact: true}).click();
}

async function selectTag(page: Page, value: string) {
  const toggle = page.getByRole('button', {name: 'Search tags', exact: true});
  if (await toggle.getAttribute('aria-expanded') !== 'true') await toggle.click();
  const input = page.getByRole('combobox', {name: 'Search tags', exact: true});
  await input.fill(value);
  await expect(page.getByRole('option', {name: value, exact: true})).toBeVisible();
  await input.press('Enter');
}

test('saved lookup covers 205 IDs independently of the first home page and limits concurrency', async ({page}) => {
  const ids = Array.from({length: 205}, (_, i) => `saved-${i}`), calls: Lookup[] = [];
  let active = 0, peak = 0, release!: () => void, listCalls = 0;
  const gate = new Promise<void>(resolve => release = resolve);
  await seedBookmarks(page, [...ids, 'recipes:saved-0', 'bad/id', 7]);
  const verify = await mockApi(page, async (route, url) => {
    if (url.pathname === '/api/recipes') {
      listCalls++;
      await route.fulfill({json: {items: Array.from({length: 60}, (_, i) => summary(`home-${i}`)), has_more: true}});
      return true;
    }
    if (url.pathname !== '/api/recipes/lookup') return false;
    const body = route.request().postDataJSON() as Lookup;
    calls.push(body); active++; peak = Math.max(peak, active);
    await gate; active--;
    await route.fulfill({json: {items: body.ids.map(id => summary(id)), unavailable_ids: []}});
    return true;
  });
  await page.goto('/');
  await expect(page.locator('.card')).toHaveCount(60);
  await page.getByRole('link', {name: 'Saved recipes', exact: true}).click();
  await expect.poll(() => calls.length).toBe(2);
  await expect(page.getByText('Loading saved recipes (0 of 3)…')).toBeVisible();
  await expect(page.getByRole('heading', {name: 'No saved recipes'})).toHaveCount(0);
  release();
  await expect(page.locator('.card')).toHaveCount(205);
  expect(calls.map(call => call.ids.length)).toEqual([100, 100, 5]);
  expect(calls.flatMap(call => call.ids)).toEqual(ids);
  expect(await page.locator('.card h3 a').allTextContents()).toEqual(ids.map(id => `Recipe ${id}`));
  expect(peak).toBe(2); expect(listCalls).toBe(1);
  await expect(page.getByRole('link', {name: 'Recipe saved-204', exact: true})).toBeVisible();
  await expect(page.getByText('2 invalid bookmarks could not be loaded.')).toBeVisible();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('notebook:bookmarks')!))).toHaveLength(208);
  verify();
});

test('saved failures retain successes, retry only failed chunks and remove unavailable only explicitly', async ({page}) => {
  const ids = Array.from({length: 101}, (_, i) => `saved-${i}`), calls: Lookup[] = [];
  let fail = true;
  await seedBookmarks(page, [...ids, 'recipes:missing']);
  const verify = await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/recipes/lookup') return false;
    const body = route.request().postDataJSON() as Lookup; calls.push(body);
    if (body.ids.includes('saved-100') && fail) {
      fail = false; await route.fulfill({status: 503, json: {detail: 'Temporary lookup outage'}});
    } else await route.fulfill({json: {items: body.ids.filter(id => id !== 'missing').map(id => summary(id)), unavailable_ids: body.ids.includes('missing') ? ['missing'] : []}});
    return true;
  });
  await page.goto('/saved');
  await expect(page.locator('.card')).toHaveCount(100);
  await expect(page.getByRole('alert')).toContainText('Temporary lookup outage');
  await page.getByRole('button', {name: 'Retry failed requests'}).click();
  await expect(page.locator('.card')).toHaveCount(101);
  expect(calls.map(call => call.ids.length)).toEqual([100, 2, 2]);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('notebook:bookmarks')!))).toContain('recipes:missing');
  await page.getByRole('button', {name: 'Remove unavailable'}).click();
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem('notebook:bookmarks')!))).toEqual(ids);
  verify();
});

test('saved tag and text refinements refetch every batch and restore filters through browser history', async ({page}) => {
  const calls: Lookup[] = [];
  await seedBookmarks(page, Array.from({length: 101}, (_, i) => `saved-${i}`));
  const verify = await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/recipes/lookup') return false;
    const body = route.request().postDataJSON() as Lookup; calls.push(body);
    await route.fulfill({json: {items: body.ids.includes('saved-100') ? [summary('saved-100', ['dinner', 'vegetarian'])] : [], unavailable_ids: []}});
    return true;
  });
  await page.goto('/saved?q=broth&tag=dinner');
  await expect(page.getByRole('link', {name: 'Recipe saved-100', exact: true})).toBeVisible();
  await selectTag(page, 'vegetarian');
  await expect(page).toHaveURL(/\/saved\?q=broth&tag=dinner&tag=vegetarian$/);
  await search(page, 'soup');
  await expect.poll(() => calls.filter(call => call.q === 'soup').length).toBe(2);
  expect(calls.filter(call => call.q === 'soup').every(call => call.tags.join() === 'dinner,vegetarian')).toBe(true);
  await page.goBack();
  await expect(page.getByRole('searchbox', {name: 'Search recipes', exact: true})).toHaveValue('broth');
  await page.locator('.active-filters').getByRole('link', {name: 'dinner', exact: true}).click();
  await expect(page).toHaveURL(/\/saved\?q=broth&tag=vegetarian$/);
  await page.getByRole('button', {name: 'Clear all'}).click();
  await expect(page).toHaveURL(/\/saved$/);
  await expect.poll(() => calls.filter(call => !call.q && !call.tags.length).length).toBe(2);
  verify();
});

test('home groups legacy classifiers once and combines full-catalog keyboard tags with text search', async ({page}) => {
  const requests: URL[] = [];
  const verify = await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/recipes') return false;
    requests.push(url);
    await route.fulfill({json: {items: [summary('legacy', ['dinner', 'breakfast']), summary('cuisine', ['american'])], has_more: false, errors: url.searchParams.get('q') === 'unknown:field' ? ['Unknown search field: unknown'] : []}});
    return true;
  });
  await page.goto('/');
  await expect(page.locator('.recipe-group').filter({has: page.getByRole('heading', {name: 'breakfast', exact: true})}).getByRole('link', {name: 'Recipe legacy'})).toBeVisible();
  await expect(page.getByRole('link', {name: 'Recipe legacy'})).toHaveCount(1);
  await expect(page.locator('.recipe-group').filter({has: page.getByRole('heading', {name: 'Other', exact: true})}).getByRole('link', {name: 'Recipe cuisine'})).toBeVisible();
  await selectTag(page, 'vegetarian');
  await selectTag(page, 'dinner');
  await search(page, 'soup');
  await expect.poll(() => requests.at(-1)?.searchParams.get('q')).toBe('soup');
  expect(requests.at(-1)?.searchParams.getAll('tag')).toEqual(['vegetarian', 'dinner']);
  await expect(page.getByRole('heading', {name: 'Results', level: 1})).toBeVisible();
  await search(page, 'unknown:field');
  await expect(page.locator('p.notice[role="status"]').filter({hasText: 'Unknown search field: unknown'})).toHaveText('Unknown search field: unknown');
  verify();
});

test('mobile navigation stays on one row and expands search over the header', async ({page}) => {
  await page.setViewportSize({width: 375, height: 812});
  const verify = await mockApi(page);
  await page.goto('/');
  const header = page.locator('.site-header');
  const searchToggle = page.getByRole('button', {name: 'Open recipe search', exact: true});
  const searchbox = page.getByRole('searchbox', {name: 'Search recipes', exact: true});
  const addRecipe = header.getByRole('link', {name: 'Add recipe', exact: true});
  await expect(searchToggle).toBeVisible();
  await expect(searchbox).toBeHidden();
  await expect(addRecipe).toBeVisible();
  await expect(addRecipe.locator('.add-label')).toBeHidden();
  const [searchBounds, addBounds] = await Promise.all([searchToggle.boundingBox(), addRecipe.boundingBox()]);
  expect(searchBounds!.x).toBeLessThan(addBounds!.x);
  expect(addBounds!.width).toBe(addBounds!.height);
  const closedHeight = (await header.boundingBox())!.height;
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await searchToggle.click();
  await expect(searchbox).toBeVisible();
  await expect(searchbox).toBeFocused();
  expect((await header.boundingBox())!.height).toBe(closedHeight);
  await searchbox.press('Escape');
  await expect(searchbox).toBeHidden();
  await expect(searchToggle).toBeFocused();

  const tagToggle = page.getByRole('button', {name: 'Search tags', exact: true});
  await expect(tagToggle).toBeInViewport();
  await expect(page.locator('.quick-tags a').first()).toBeVisible();
  const bounds = await page.locator('.quick-tags').evaluate(element => {
    const row = element.getBoundingClientRect();
    return [...element.querySelectorAll('a')].filter(link => getComputedStyle(link).display !== 'none').map(link => {
      const rect = link.getBoundingClientRect();
      return {top: rect.top, inside: rect.left >= row.left && rect.right <= row.right, inert: link.inert, hidden: getComputedStyle(link).visibility === 'hidden'};
    });
  });
  expect(new Set(bounds.map(value => value.top)).size).toBeLessThanOrEqual(1);
  expect(bounds.every(value => value.inside || (value.inert && value.hidden))).toBe(true);
  await tagToggle.focus(); await page.keyboard.press('Enter');
  const tagInput = page.getByRole('combobox', {name: 'Search tags', exact: true});
  await tagInput.fill('vegetarian');
  await expect(page.getByRole('option', {name: 'vegetarian', exact: true})).toBeVisible();
  await tagInput.press('Enter');
  await expect(page.locator('.active-filters').getByRole('link', {name: 'Remove vegetarian', exact: true})).toBeVisible();
  await addRecipe.click();
  await expect(page).toHaveURL(/\/new$/);
  await expect(page.getByLabel('Paste or write your recipe')).toBeVisible();
  await expect(page.getByRole('button', {name: 'Start a new recipe', exact: true})).toHaveCount(0);
  verify();
});

test('home and search result headings stay left-aligned on mobile', async ({page}) => {
  await page.setViewportSize({width: 375, height: 812});
  const verify = await mockApi(page);
  await page.goto('/');
  const heading = page.locator('.page-heading h1');
  const container = page.locator('.page-heading');
  await expect(heading).toHaveText(copy.home_title);
  const leftEdges = async () => Promise.all([container, heading].map(async element => (await element.boundingBox())!.x));
  expect(await leftEdges()).toEqual([16, 16]);
  await page.getByRole('searchbox', {name: 'Search recipes', exact: true}).fill('soup');
  await expect(page).toHaveURL('/?q=soup');
  await expect(heading).toHaveText('Results');
  expect(await leftEdges()).toEqual([16, 16]);
  verify();
});

test('site copy failures keep neutral browse content and retry refreshes bound text', async ({page}) => {
  let attempts = 0;
  const verify = await mockApi(page, async (route, url) => {
    if (url.pathname !== '/api/site-settings') return false;
    attempts++;
    await route.fulfill(attempts === 1 ? {status: 503, json: {detail: 'Site text temporarily unavailable'}} : {json: {revision: 1, copy}});
    return true;
  });
  await page.goto('/');
  await expect(page.getByRole('heading', {name: 'Recipes', level: 1})).toBeVisible();
  await expect(page.getByRole('button', {name: 'Search tags', exact: true})).toBeVisible();
  await page.getByRole('button', {name: 'Retry site text'}).click();
  await expect(page.getByRole('heading', {name: copy.home_title, level: 1})).toBeVisible();
  await expect(page.locator('footer')).toHaveText(copy.footer_text);
  expect(attempts).toBe(2);
  verify();
});

for (const admin of [false, true]) {
  test(`named profile menu binds server admin=${admin} without a separate login`, async ({page}) => {
    const verify = await mockApi(page, async (route, url) => {
      if (url.pathname !== '/api/session') return false;
      await route.fulfill({json: {user: {id: 'user-1', display_name: 'Browser cook', state: 'active', photo_trusted: false}, device_id: 'device-1', admin, csrf_token: 'browser-test'}});
      return true;
    });
    await page.goto('/');
    const trigger = page.getByRole('button', {name: 'Profile menu'});
    await expect(trigger).toContainText('Browser cook');
    await trigger.click();
    await expect(page.getByRole('menuitem', {name: 'My profile', exact: true})).toHaveAttribute('href', '/profile');
    await expect(page.getByRole('menuitem', {name: 'Admin', exact: true})).toHaveCount(admin ? 1 : 0);
    await expect(page.getByRole('menuitem', {name: 'Set name', exact: true})).toHaveCount(0);
    verify();
  });
}

test('site copy and anonymous profile bindings render and named drafts resume independently', async ({page}) => {
  const drafts = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'].map((id, i) => ({version: 2, id, name: `Local draft ${i + 1}`, updatedAt: '2026-09-01T12:00:00Z', publishKey: `key-${i}`, draft: {...blank(), title: `Draft title ${i + 1}`, source_text: `  Exact draft ${i + 1}.\n`}}));
  await page.addInitScript(drafts => {for (const draft of drafts) localStorage.setItem(`notebook:draft:v2:${draft.id}`, JSON.stringify(draft));}, drafts);
  const verify = await mockApi(page);
  await page.goto('/');
  await expect(page.getByRole('heading', {name: copy.home_title, level: 1})).toBeVisible();
  await expect(page.locator('h1 b')).toHaveCount(0);
  await expect(page.getByText(copy.home_intro, {exact: true})).toBeVisible();
  await expect(page.locator('footer')).toHaveText(copy.footer_text);
  await expect(page.locator('.brand')).toContainText(copy.site_title);
  await expect(page.locator('.brand')).toContainText(copy.site_tagline);
  const menu = page.getByRole('button', {name: 'Profile menu'});
  await expect(menu).toContainText('Anonymous');
  await menu.focus(); await page.keyboard.press('Enter');
  await expect(page.getByRole('menuitem', {name: 'Set name', exact: true})).toBeVisible();
  await expect(page.getByRole('menuitem', {name: 'Admin', exact: true})).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(menu).toHaveAttribute('aria-expanded', 'false');
  const region = page.getByRole('region', {name: 'Your drafts'});
  await expect(region.locator('article')).toHaveCount(2);
  await expect(region.getByText('Draft', {exact: true})).toHaveCount(2);
  await expect(region.locator('img')).toHaveCount(0);
  await region.locator('article').filter({hasText: 'Local draft 1'}).getByRole('link', {name: 'Resume'}).click();
  await expect(page).toHaveURL(new RegExp(`/new\\?draft=${drafts[0].id}$`));
  await expect(page.getByLabel('Recipe title', {exact: true})).toHaveValue('Draft title 1');
  await expect(page.getByLabel('Paste or write your recipe', {exact: true})).toHaveValue(drafts[0].draft.source_text);
  await page.goBack();
  await page.getByRole('region', {name: 'Your drafts'}).locator('article').filter({hasText: 'Local draft 2'}).getByRole('link', {name: 'Resume'}).click();
  await expect(page.getByLabel('Recipe title', {exact: true})).toHaveValue('Draft title 2');
  await expect(page.getByLabel('Paste or write your recipe', {exact: true})).toHaveValue(drafts[1].draft.source_text);
  verify();
});
