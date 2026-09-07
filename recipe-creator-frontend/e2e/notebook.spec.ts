import { test, expect, createProfile, mutate, session, source, origin } from './fixtures';

test('reading and bookmarks do not create an identity; legacy recipe links resolve', async ({page, context, second, recipe}) => {
  await second.page.goto(`/#/view/${encodeURIComponent(recipe.id)}`);
  await expect(second.page).toHaveURL(`${origin}/recipes/${encodeURIComponent(recipe.id)}`);
  await expect(second.page.getByRole('heading', {name: recipe.title})).toBeVisible();
  expect(await second.page.locator('.recipe-body').textContent()).toBe(source);
  await second.page.getByRole('button', {name: 'Save for later'}).click();
  expect((await session(second.context.request)).user).toBeNull();
  await second.page.goto('/profile');
  await expect(second.page.getByText(/Reading and bookmarking do not create a profile/ )).toBeVisible();
  expect((await session(second.context.request)).device_id).toBeNull();
  expect((await session(context.request)).user).not.toBeNull();
});

test('publishes without signup and keeps exact text when organizing fails', async ({page, context}) => {
  await page.goto('/new');
  await page.getByLabel('Recipe title').fill(`Text fallback ${crypto.randomUUID()}`);
  await page.getByLabel('Recipe body').fill(source);
  await page.getByRole('button', {name: 'Switch to Structured'}).click();
  await expect(page.getByRole('alert')).toContainText('Your text is safe');
  expect((await session(context.request)).user).toBeNull();
  await page.getByRole('button', {name: 'Publish recipe'}).click();
  await page.getByLabel('Your display name').fill('Text-only cook');
  await page.getByLabel('Your display name').press('Enter');
  await expect(page.getByText('Profile ready. Review your recipe, then publish.')).toBeVisible();
  await page.getByRole('button', {name: 'Publish recipe'}).click();
  await expect(page).toHaveURL(/\/recipes\//);
  expect(await page.locator('.recipe-body').textContent()).toBe(source);
  const recipe = await (await context.request.get(`/api${new URL(page.url()).pathname}`)).json();
  expect(recipe.mode).toBe('text');
  expect(recipe.source_text).toBe(source);
  const deleted = await mutate(context.request, `/recipes/${encodeURIComponent(recipe.id)}?expected_revision=${recipe.revision}`, undefined, 'DELETE');
  expect(deleted.status(), await deleted.text()).toBe(204);
});

test('two browsers enforce owner edits, deletes, and server-filtered My recipes', async ({page, context, second, recipe, owner}) => {
  await createProfile(second.context.request, 'Other owner');
  await second.page.goto(`/recipes/${encodeURIComponent(recipe.id)}/edit`);
  await expect(second.page.getByText('You can read this recipe, but only its owner or an administrator can edit it.')).toBeVisible();
  const path = `/recipes/${encodeURIComponent(recipe.id)}`;
  expect([403, 404]).toContain((await mutate(second.context.request, path, {title: 'Hijacked', mode: 'text', source_text: 'Changed', expected_revision: recipe.revision}, 'PUT')).status());
  expect([403, 404]).toContain((await mutate(second.context.request, `${path}?expected_revision=${recipe.revision}`, undefined, 'DELETE')).status());
  const other = await session(second.context.request);
  const filtered = await (await second.context.request.get(`/api/recipes?owner_id=${encodeURIComponent(other.user!.id)}`)).json();
  expect(filtered.items.every((item: {owner_id: string}) => item.owner_id === other.user!.id)).toBe(true);
  await page.goto('/profile');
  await expect(page.getByText('(this device)', {exact: false})).toBeVisible();
  await page.getByRole('button', {name: 'My recipes', exact: true}).click();
  await expect(page.getByRole('link', {name: recipe.title})).toBeVisible();
  expect((await (await context.request.get(`/api${path}`)).json()).owner_id).toBe(owner.user!.id);
});

test('pairing requires source approval and explicit destination switch', async ({page, context, owner, second}) => {
  await createProfile(second.context.request, 'Destination cook');
  await page.goto('/profile');
  await page.getByRole('button', {name: 'Create pairing code'}).click();
  const code = await page.locator('.pair-code').textContent();
  await second.page.goto('/profile');
  await second.page.getByLabel('Pairing code', {exact: true}).fill(code!);
  await second.page.getByRole('button', {name: 'Request connection'}).click();
  await expect(second.page.getByText('Status: requested', {exact: true})).toBeVisible();
  expect((await session(second.context.request)).user!.id).not.toBe(owner.user!.id);
  page.once('dialog', dialog => dialog.accept());
  await page.getByRole('button', {name: 'Confirm this device request'}).click();
  const complete = second.page.getByRole('button', {name: 'Complete connection'});
  await expect(complete).toBeDisabled();
  await second.page.getByLabel('I want to switch this browser to this profile').check();
  await complete.click();
  await expect(second.page.getByText('This device is connected. Admin access was not transferred.')).toBeVisible();
  const destination = await session(second.context.request);
  expect(destination.user!.id).toBe(owner.user!.id);
  expect(destination.admin).toBe(false);
  expect((await (await context.request.get('/api/devices')).json()).devices.filter((device: {revoked_at: string | null}) => !device.revoked_at)).toHaveLength(2);
});

test('admin sees raw pending photos and moderates the actual submission', async ({page, context, second, recipe, adminPassword}) => {
  await page.goto(`/recipes/${encodeURIComponent(recipe.id)}`);
  await page.getByRole('button', {name: 'View photos / add yours'}).click();
  const png = await page.evaluate(() => {const canvas = document.createElement('canvas'); canvas.width = 64; canvas.height = 64; const paint = canvas.getContext('2d')!; paint.fillStyle = '#c63'; paint.fillRect(0, 0, 64, 64); return canvas.toDataURL('image/png').split(',')[1];});
  await page.getByLabel('Choose photo').setInputFiles({name: 'dinner.png', mimeType: 'image/png', buffer: Buffer.from(png, 'base64')});
  await expect(page.getByText(/Ready to upload:/)).toBeVisible();
  await page.getByLabel('Caption').fill(`Dinner ${recipe.id}`);
  await page.getByRole('button', {name: /Submit photo/}).click();
  await expect(page.getByText('Private submission: pending')).toBeVisible();
  await second.page.goto('/admin');
  await second.page.getByLabel('Admin password').fill(adminPassword);
  await second.page.getByRole('button', {name: 'Log in', exact: true}).click();
  const photo = second.page.locator('figure').filter({hasText: `Dinner ${recipe.id}`});
  await expect(photo).toContainText('pending');
  await photo.getByRole('button', {name: 'Approve', exact: true}).click();
  await expect(photo).toContainText('approved');
  await page.getByRole('button', {name: 'Refresh submissions'}).click();
  await expect(page.locator('figure').filter({hasText: `Dinner ${recipe.id}`})).toContainText('approved');
  expect((await session(second.context.request)).user).toBeNull();
});
