import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import Browse from './Browse.svelte';
import { appState, DEFAULT_SITE_COPY, setSiteCopy } from './app-state.svelte';
import { createDraft, draftHref, renameDraft } from './drafts';

const summary = (id: string, tags: string[] = []) => ({id, title: `Recipe ${id}`, tags, description: '', thumbnail_photo_id: null});
type Lookup = {ids: string[]; q?: string; tags: string[]};
function mockApi(handler: (url: URL, init?: RequestInit) => unknown | Promise<unknown>) {
  const fetcher = vi.fn(async (input: string, init?: RequestInit) => {
    const url = new URL(input, location.origin);
    const result = url.pathname === '/api/session' ? {user: null, device_id: null, admin: false, csrf_token: 'test'}
      : url.pathname === '/api/tags' ? {tags: ['dinner', 'vegetarian'], classifier_tags: ['breakfast', 'lunch', 'dinner', 'dessert']}
      : await handler(url, init);
    return result instanceof Response ? result : new Response(JSON.stringify(result));
  });
  vi.stubGlobal('fetch', fetcher);
  return fetcher;
}
beforeEach(() => {
  appState.identity = null; setSiteCopy({...DEFAULT_SITE_COPY});
});

it('refetches all saved chunks on refinements and ignores a late previous generation', async () => {
  localStorage.setItem('notebook:bookmarks', JSON.stringify(Array.from({length: 101}, (_, i) => `id-${i}`)));
  const calls: Lookup[] = [], old: Array<() => void> = [];
  mockApi(async (url, init) => {
    expect(url.pathname).toBe('/api/recipes/lookup');
    const body = JSON.parse(String(init?.body)) as Lookup; calls.push(body);
    if (body.q === 'old') await new Promise<void>(resolve => old.push(resolve));
    return {items: body.ids.includes('id-100') ? [summary(body.q === 'old' ? 'stale' : 'fresh')] : [], unavailable_ids: []};
  });
  const component = render(Browse, {query: 'old', selectedTags: ['dinner'], savedOnly: true});
  await waitFor(() => expect(old).toHaveLength(2));
  expect(screen.queryByText('No saved recipes')).not.toBeInTheDocument();
  await component.rerender({query: 'soup', selectedTags: ['dinner', 'vegetarian'], savedOnly: true});
  await screen.findByRole('link', {name: 'Recipe fresh'});
  expect(calls.filter(call => call.q === 'soup').map(call => [call.ids.length, call.tags])).toEqual([[100, ['dinner', 'vegetarian']], [1, ['dinner', 'vegetarian']]]);
  old.forEach(resolve => resolve());
  await waitFor(() => expect(screen.queryByRole('status')).not.toBeInTheDocument());
  expect(screen.queryByRole('link', {name: 'Recipe stale'})).not.toBeInTheDocument();
});

it('does not resurrect explicitly removed unavailable bookmarks when filters change', async () => {
  localStorage.setItem('notebook:bookmarks', JSON.stringify(['kept', 'recipes:missing']));
  const calls: Lookup[] = [];
  mockApi((_url, init) => {
    const body = JSON.parse(String(init?.body)) as Lookup; calls.push(body);
    return {items: [summary('kept')], unavailable_ids: body.ids.includes('missing') ? ['missing'] : []};
  });
  const component = render(Browse, {query: '', savedOnly: true});
  await fireEvent.click(await screen.findByRole('button', {name: 'Remove unavailable'}));
  expect(JSON.parse(localStorage.getItem('notebook:bookmarks')!)).toEqual(['kept']);
  await component.rerender({query: 'soup', savedOnly: true});
  await waitFor(() => expect(calls).toHaveLength(2));
  expect(calls[1].ids).toEqual(['kept']);
  expect(screen.queryByText(/saved recipe is unavailable/)).not.toBeInTheDocument();
});

it('binds live home copy and same-tab draft updates without treating drafts as published recipes', async () => {
  const draft = createDraft('First local draft');
  mockApi(() => ({items: [], has_more: false}));
  const component = render(Browse, {query: ''});
  await screen.findByRole('heading', {name: 'First local draft'});
  expect(screen.getByRole('link', {name: 'Resume'})).toHaveAttribute('href', draftHref(draft.id));
  expect(screen.getByRole('region', {name: 'Your drafts'}).querySelector('img')).toBeNull();
  renameDraft(draft.id, 'Renamed local draft');
  await screen.findByRole('heading', {name: 'Renamed local draft'});
  setSiteCopy({...DEFAULT_SITE_COPY, home_title: '<em>Neighbors</em>', home_intro: 'Fresh home introduction'});
  const title = await screen.findByRole('heading', {name: '<em>Neighbors</em>'});
  expect(title.querySelector('em')).toBeNull();
  expect(screen.getByText('Fresh home introduction')).toBeVisible();
  await component.rerender({query: 'soup'});
  await screen.findByRole('heading', {name: 'Results', level: 1});
  expect(screen.queryByText('Fresh home introduction')).not.toBeInTheDocument();
});

it('applies repeated tags and text before pagination and resets offset when refinements change', async () => {
  const urls: URL[] = [];
  mockApi(url => {
    urls.push(url);
    return {items: [summary(`page-${url.searchParams.get('offset')}-${url.searchParams.get('q')}`)], has_more: url.searchParams.get('offset') === '0'};
  });
  const component = render(Browse, {query: 'soup', selectedTags: ['dinner', 'vegetarian']});
  await fireEvent.click(await screen.findByRole('button', {name: 'Load more recipes'}));
  await screen.findByRole('link', {name: 'Recipe page-1-soup'});
  expect(urls.map(url => url.searchParams.get('offset'))).toEqual(['0', '1']);
  expect(urls.every(url => url.searchParams.getAll('tag').join() === 'dinner,vegetarian')).toBe(true);
  await component.rerender({query: 'broth', selectedTags: ['dinner']});
  await screen.findByRole('link', {name: 'Recipe page-0-broth'});
  expect(screen.queryByRole('link', {name: 'Recipe page-1-soup'})).not.toBeInTheDocument();
  expect(urls.at(-1)?.searchParams.getAll('tag')).toEqual(['dinner']);
});
