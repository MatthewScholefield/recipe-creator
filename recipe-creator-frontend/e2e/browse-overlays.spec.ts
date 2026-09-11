import { test, expect, type Locator } from '@playwright/test';
import { blank, ingredient } from '../src/recipe';

const recipe = {
  ...blank('structured'),
  id: 'mobile-overlays',
  title: 'Mobile soup',
  revision: 1,
  owner_id: 'cook',
  author_name: 'Cook',
  can_edit: false,
  enrichment_status: 'complete',
  directions: 'Simmer.',
  ingredient_groups: [{
    id: 'group',
    name: 'Ingredients',
    ingredients: [{
      ...ingredient(),
      id: 'flour',
      quantity: '1',
      unit: 'cup',
      name: 'flour',
      original_text: '1 cup flour',
      grams: {amount: 120, low: null, high: null, estimated: false, basis: 'Measured flour'},
    }],
  }],
};

async function expectInsideViewport(locator: Locator, width: number) {
  const box = await locator.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.x).toBeGreaterThanOrEqual(11);
  expect(box!.x + box!.width).toBeLessThanOrEqual(width - 11);
}

test('ingredient settings and tooltips stay inside a narrow viewport', async ({page}) => {
  const width = 320;
  await page.setViewportSize({width, height: 700});
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    const json = path === '/api/session'
      ? {user: null, device_id: null, admin: false, csrf_token: 'test'}
      : path === '/api/site-settings'
        ? {revision: 0, copy: {site_title: 'Recipes', site_tagline: '', home_title: 'Recipes', home_intro: '', footer_text: ''}}
        : path === '/api/recipes/mobile-overlays'
          ? recipe
          : {items: []};
    return route.fulfill({json});
  });

  await page.goto('/recipes/mobile-overlays');
  await page.getByRole('button', {name: 'Adjust ingredient scale'}).click();
  const menu = page.getByRole('menu', {name: 'Ingredient settings'});
  await expect(menu).toBeVisible();
  await expectInsideViewport(menu, width);

  await page.getByRole('tab', {name: 'Grams'}).click();
  await page.keyboard.press('Escape');
  await page.getByText('120 g').hover();
  const tooltip = page.getByRole('tooltip');
  await expect(tooltip).toBeVisible();
  await expectInsideViewport(tooltip, width);
});
