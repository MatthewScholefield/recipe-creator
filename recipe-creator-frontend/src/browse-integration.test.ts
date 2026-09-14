import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import Browse from './Browse.svelte';
import { appState, DEFAULT_SITE_COPY, setSiteCopy } from './app-state.svelte';
import { createDraft, draftHref, saveDraft } from './drafts';

const summary = (id: string, tags: string[] = [], total_views = 0, search_score: number | null = null) => ({id, title: `Recipe ${id}`, tags, description: '', thumbnail_photo_id: null, owner_id:null, author_name:null, total_views, unique_viewers:0, search_score});
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

it('shows draft recipe titles and relative update times, then reflects title changes', async () => {
  const draft = createDraft();
  draft.draft.title = 'Tomato soup';
  draft.updatedAt = new Date(Date.now() - 10 * 60 * 1000).toISOString();
  saveDraft(draft);
  mockApi(() => ({items: [], has_more: false}));
  const component = render(Browse, {query: ''});
  const draftTitle = await screen.findByRole('link', {name: 'Tomato soup'});
  expect(draftTitle).toHaveAttribute('href', draftHref(draft.id));
  const draftRegion = screen.getByRole('region', {name: 'Drafts'});
  expect(draftRegion.querySelector('.card')).toHaveClass('draft');
  expect(draftRegion.querySelector('time')).toHaveAttribute('datetime', draft.updatedAt);
  expect(draftRegion).toHaveTextContent('Updated 10 minutes ago');
  expect(draftRegion.querySelector('img')).toBeNull();
  draft.draft.title = 'Renamed local draft';
  saveDraft(draft);
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
    return {items: [summary(`page-${url.searchParams.get('offset')}-${url.searchParams.get('q')}`)], offset: Number(url.searchParams.get('offset')), popular: [], errors: [], has_more: url.searchParams.get('offset') === '0'};
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

it('renders Popular only at home and keeps live pagination offsets despite duplicates', async () => {
  const urls: URL[] = [];
  mockApi(url => {
    urls.push(url);
    const offset = Number(url.searchParams.get('offset'));
    if (url.searchParams.get('q')) return {items:[summary('search')],offset:0,popular:[],errors:[],has_more:false};
    if (offset === 0) return {
      items:[summary('a',['dinner'],5),summary('b',['dinner'],1)],
      popular:[summary('popular-1'),summary('popular-2'),summary('popular-3')],
      offset:0,errors:[],has_more:true,
    };
    if (offset === 2) return {
      items:[summary('a',['dinner'],0),summary('c',['dinner'],10)],
      popular:[],offset:2,errors:[],has_more:true,
    };
    return {items:[summary('d',['dinner'],3)],popular:[],offset:4,errors:[],has_more:false};
  });
  const component = render(Browse,{query:''});
  expect(await screen.findByRole('heading',{name:'Popular'})).toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Load more recipes'}));
  await screen.findByRole('link',{name:'Recipe c'});
  expect(screen.getAllByRole('link',{name:'Recipe a'})).toHaveLength(1);
  const dinner = screen.getByRole('heading',{name:'dinner'}).closest('section')!;
  expect(within(dinner).getAllByRole('link').map(link => link.textContent)).toEqual(['Recipe c','Recipe b','Recipe a']);
  await fireEvent.click(screen.getByRole('button',{name:'Load more recipes'}));
  await screen.findByRole('link',{name:'Recipe d'});
  expect(urls.filter(url => url.pathname === '/api/recipes').map(url => url.searchParams.get('offset'))).toEqual(['0','2','4']);
  await component.rerender({query:'soup'});
  await screen.findByRole('link',{name:'Recipe search'});
  expect(screen.queryByRole('heading',{name:'Popular'})).not.toBeInTheDocument();
});

it('preserves globally ranked server order across ordinary pages', async () => {
  mockApi(url => {
    const offset = Number(url.searchParams.get('offset'));
    return offset === 0
      ? {items: [summary('z', [], 0, 4.9), summary('a', [], 0, 3.8)], has_more: true}
      : {items: [summary('m', [], 0, 2.7)], has_more: false};
  });
  render(Browse, {query: 'soup'});
  await screen.findByRole('link', {name: 'Recipe z'});
  expect(screen.getAllByRole('link', {name: /^Recipe [zam]$/}).map(link => link.textContent?.trim())).toEqual(['Recipe z', 'Recipe a']);
  await fireEvent.click(screen.getByRole('button', {name: 'Load more recipes'}));
  await screen.findByRole('link', {name: 'Recipe m'});
  expect(screen.getAllByRole('link', {name: /^Recipe [zam]$/}).map(link => link.textContent?.trim())).toEqual(['Recipe z', 'Recipe a', 'Recipe m']);
});

it('globally ranks saved batches after retry and uses lexical IDs for score ties', async () => {
  localStorage.setItem('notebook:bookmarks', JSON.stringify(Array.from({length: 101}, (_, i) => `id-${i}`)));
  let failedOnce = false;
  mockApi((_url, init) => {
    const body = JSON.parse(String(init?.body)) as Lookup;
    if (body.ids.includes('id-100')) {
      if (!failedOnce) { failedOnce = true; throw new Error('temporary'); }
      return {items: [summary('z', [], 0, 4.9)], unavailable_ids: []};
    }
    return {items: [summary('b', [], 0, 3.5), summary('a', [], 0, 3.5)], unavailable_ids: []};
  });
  render(Browse, {query: 'soup', savedOnly: true});
  await screen.findByRole('button', {name: 'Retry failed requests'});
  expect(screen.getAllByRole('link', {name: /^Recipe [ab]$/}).map(link => link.textContent?.trim())).toEqual(['Recipe a', 'Recipe b']);
  await fireEvent.click(screen.getByRole('button', {name: 'Retry failed requests'}));
  await screen.findByRole('link', {name: 'Recipe z'});
  expect(screen.getAllByRole('link', {name: /^Recipe [zab]$/}).map(link => link.textContent?.trim())).toEqual(['Recipe z', 'Recipe a', 'Recipe b']);
});

it('retains bookmark order for saved results without search scores', async () => {
  localStorage.setItem('notebook:bookmarks', JSON.stringify(['z', 'a']));
  mockApi(() => ({items: [summary('a'), summary('z')], unavailable_ids: []}));
  render(Browse, {query: '', savedOnly: true});
  await screen.findByRole('link', {name: 'Recipe z'});
  expect(screen.getAllByRole('link', {name: /^Recipe [za]$/}).map(link => link.textContent?.trim())).toEqual(['Recipe z', 'Recipe a']);
});
