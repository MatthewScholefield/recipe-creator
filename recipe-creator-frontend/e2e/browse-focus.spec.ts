import { test, expect } from '@playwright/test';

test('profile dropdown restores keyboard focus to its custom trigger on Escape', async ({page}) => {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    const json = path === '/api/session' ? {user: null, device_id: null, admin: false, csrf_token: 'test'}
      : path === '/api/site-settings' ? {revision: 0, copy: {site_title: 'Recipes', site_tagline: '', home_title: 'Recipes', home_intro: '', footer_text: ''}}
      : path === '/api/tags' ? {tags: [], classifier_tags: ['breakfast', 'lunch', 'dinner', 'dessert']}
      : {items: [], has_more: false};
    return route.fulfill({json});
  });
  await page.goto('/');
  const trigger = page.getByRole('button', {name: 'Profile menu'});
  await trigger.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('menuitem', {name: 'Set name', exact: true})).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(trigger).toHaveAttribute('aria-expanded', 'false');
  await expect(trigger).toBeFocused();
});
