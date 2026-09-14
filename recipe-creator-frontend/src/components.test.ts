import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, expect, it, vi } from 'vitest';
import { appState, DEFAULT_SITE_COPY, setSiteCopy } from './app-state.svelte';
beforeEach(() => {appState.identity = null; setSiteCopy({...DEFAULT_SITE_COPY});});
import Editor from './Editor.svelte';
import Browse from './Browse.svelte';
import Detail from './Detail.svelte';
import Profile from './Profile.svelte';
import Admin from './Admin.svelte';
import { blank, ingredient } from './recipe';
import { listDrafts, readDraft } from './drafts';
async function awaitBlankEditor() {
  await screen.findByLabelText('Paste or write your recipe');
}
const identity = {user:{id:'u1',display_name:'Cook',state:'active',photo_trusted:false},device_id:'d1',admin:false,csrf_token:'csrf'};
function mockApi(handler: (url: string, init?: RequestInit) => unknown | Promise<unknown>) {const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {const body = String(url) === '/api/tags' ? {tags:[]} : await handler(String(url),init); return body instanceof Response ? body : new Response(JSON.stringify(body));}); vi.stubGlobal('fetch',fetcher); return fetcher;}
const recipe = {...blank('structured'),id:'r1',title:'Soup',revision:2,owner_id:'u1',author_name:'Cook',can_edit:true,directions:'Simmer 20 minutes at 180°C.',ingredient_groups:[{id:'g1',name:'',ingredients:[{...ingredient(),id:'i1',name:'stock',quantity:'1/2',unit:'cup',original_text:'½ cup stock'}]}]};
it('uses the actual admin photo wrapper, raw status, and moderation route', async () => {
  const fetcher = mockApi(url => url === '/api/session' ? {...identity,admin:true} : url === '/api/admin/photos' ? {photos:[{id:'p1',recipe_id:'r1',uploader_id:'u1',caption:'Dinner photo',status:'pending'}]} : {status:'approved'});
  render(Admin); await screen.findByText(/u1 · pending/); await fireEvent.click(screen.getByRole('button',{name:'Approve'}));
  await waitFor(() => expect(fetcher.mock.calls.some(([url,init]) => url === '/api/admin/photos/p1/moderate' && JSON.parse(init!.body as string).state === 'approved')).toBe(true));
  expect(await screen.findByText(/u1 · approved/)).toBeInTheDocument();
});
it('uses device wrappers and requests server-owned summaries for My recipes', async () => {
  const fetcher = mockApi(url => url === '/api/session' ? identity : url === '/api/devices' ? {devices:[{id:'d1',current:true,revoked_at:null}]} : {items:[recipe],has_more:false});
  render(Profile,{consumeToken:vi.fn()}); await screen.findByText(/Browser \(this device\)/); await fireEvent.click(screen.getByRole('button',{name:'My recipes'}));
  expect(await screen.findByRole('link',{name:/Soup/})).toBeInTheDocument(); expect(fetcher.mock.calls.some(([url]) => url === '/api/recipes?owner_id=u1&offset=0&limit=100')).toBe(true);
});
it('does not navigate after a publishing editor is unmounted', async () => {
  let finish!: (value: unknown) => void;
  const fetcher = mockApi(url => url === '/api/session' ? identity : url === '/api/recipes' ? new Promise(resolve => finish = resolve) : {});
  const navigate = vi.fn(); const view = render(Editor,{navigate}); await awaitBlankEditor();
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Soup'}});
  await waitFor(() => expect(listDrafts()[0]).toBeDefined());
  const [local] = listDrafts(); expect(local).toBeDefined();
  navigate.mockClear();
  await fireEvent.click(screen.getByRole('button',{name:'Publish recipe'}));
  await waitFor(() => expect(finish).toBeTypeOf('function')); view.unmount(); finish(recipe); await new Promise(resolve => setTimeout(resolve,20)); expect(navigate).not.toHaveBeenCalled();
  expect(readDraft(local.id)?.draft.title).toBe('Soup');
  expect(listDrafts().map(draft => draft.id)).toEqual([local.id]);
  expect(fetcher.mock.calls.filter(([url]) => url === '/api/recipes')).toHaveLength(1);
});
it('shows a recovered edit as a card and removes it after submission', async () => {
  localStorage.setItem('notebook:draft:r1',JSON.stringify({draft:{...blank(),title:'Recovered soup',source_text:'  exact draft\n\n'},revision:1,key:'retry-key',saved:new Date(Date.now() - 24 * 60 * 1000).toISOString()}));
  const fetcher = mockApi((url,init) => url === '/api/session' ? identity : recipe);
  render(Editor,{recipeId:'r1',navigate:vi.fn()});
  expect(await screen.findByRole('heading',{name:'Restore draft?'})).toBeInTheDocument();
  expect(screen.getByText('Recovered soup')).toBeInTheDocument();
  expect(screen.getByText('You edited')).toBeInTheDocument();
  expect(screen.getByRole('heading',{name:'Changes in this draft'})).toBeInTheDocument();
  expect(screen.getByLabelText('Draft changes to Title')).toHaveTextContent('-Soup');
  expect(screen.getByLabelText('Draft changes to Title')).toHaveTextContent('+Recovered soup');
  expect(screen.queryByLabelText('Recipe title')).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Edit draft'}));
  expect(screen.getByLabelText('Paste or write your recipe')).toHaveValue('  exact draft\n\n');
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  await waitFor(() => expect(fetcher.mock.calls.some(([,init]) => init?.method === 'PUT')).toBe(true));
  await waitFor(() => expect(localStorage.getItem('notebook:draft:r1')).toBeNull());
});
it('discards a recovered edit without leaving the form disabled', async () => {
  localStorage.setItem('notebook:draft:r1',JSON.stringify({draft:{...blank(),title:'Discard me'},revision:1,key:'discard-key',saved:new Date().toISOString()}));
  mockApi((url) => url === '/api/session' ? identity : recipe);
  render(Editor,{recipeId:'r1',navigate:vi.fn()});
  await screen.findByRole('heading',{name:'Restore draft?'});
  await fireEvent.click(screen.getByRole('button',{name:'Discard draft'}));
  expect(localStorage.getItem('notebook:draft:r1')).toBeNull();
  expect(await screen.findByLabelText('Recipe title')).toHaveValue('Soup');
  expect(screen.getByText('Draft discarded. You are editing the current recipe.')).toBeInTheDocument();
});
it('keeps text publishable during a parser outage', async () => {
  const fetcher = mockApi(url => url === '/api/session' ? identity : url === '/api/parse' ? new Response(JSON.stringify({detail:'AI unavailable'}),{status:503}) : {...recipe,id:'saved'});
  const navigate = vi.fn(); render(Editor,{navigate}); await awaitBlankEditor(); await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Dinner'}}); await fireEvent.input(screen.getByLabelText('Paste or write your recipe'),{target:{value:'Original recipe'}}); await fireEvent.click(screen.getByRole('button',{name:'Organize'})); expect(await screen.findByRole('alert')).toHaveTextContent('Your text is safe'); expect(screen.getByLabelText('Paste or write your recipe')).toHaveValue('Original recipe'); expect(screen.getByRole('button',{name:'Publish recipe'})).toBeEnabled();
  await fireEvent.click(screen.getByRole('button',{name:'Publish recipe'}));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith('/recipes/saved'));
  const write = fetcher.mock.calls.find(([url]) => url === '/api/recipes');
  expect(JSON.parse(write![1]!.body as string)).toMatchObject({mode:'text',source_text:'Original recipe'});
});
it('selects searchable merge profiles and invalidates an old preview', async () => {
  const candidates = ['Source','Target','Other'].map(display_name => ({...identity.user,id:display_name.toLowerCase(),display_name,last_login_at:null}));
  const fetcher = mockApi(url => url === '/api/session' ? {...identity,admin:true} : url.startsWith('/api/admin/users?') ? {items:candidates,has_more:false} : url.startsWith('/api/admin/merge/preview?') ? {counts:{recipes:3}} : {photos:[]});
  render(Admin); await fireEvent.click(await screen.findByRole('button',{name:'merge'}));
  const source = screen.getByRole('combobox',{name:'Source profile (will merge into target)'});
  const target = screen.getByRole('combobox',{name:'Target profile (survives)'});
  await fireEvent.focus(source); await fireEvent.click(await screen.findByRole('option',{name:/^Source /}));
  await fireEvent.focus(target); await fireEvent.click(await screen.findByRole('option',{name:/^Target /}));
  await fireEvent.click(screen.getByRole('button',{name:'Preview merge'}));
  expect(await screen.findByRole('button',{name:'Confirm profile merge'})).toBeInTheDocument();
  expect(fetcher.mock.calls.some(([url]) => url === '/api/admin/merge/preview?source_id=source&target_id=target')).toBe(true);
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('eligible_merge=true'))).toBe(true);
  await fireEvent.focus(source); await fireEvent.click(await screen.findByRole('option',{name:/^Other /}));
  expect(screen.queryByRole('button',{name:'Confirm profile merge'})).not.toBeInTheDocument();
});
it('browses summaries without a reactive request loop or identity creation', async () => {
  const fetcher = mockApi(url => url.includes('/tags') ? {tags:['dinner']} : {items:[{id:'r1',title:'Soup',description:'Warm',tags:['dinner'],author_name:null}],has_more:false,errors:[]});
  render(Browse,{query:''}); expect(await screen.findByRole('link',{name:'Soup'})).toBeInTheDocument(); await new Promise(resolve => setTimeout(resolve,30)); expect(fetcher).toHaveBeenCalledTimes(2); expect(fetcher.mock.calls.some(([url]) => String(url).includes('/session'))).toBe(false);
});
it('ignores a stale parse after typing and allows a text-only publish', async () => {
  let resolveParse!: (value: unknown) => void;
  const fetcher = mockApi(url => url === '/api/session' ? identity : url === '/api/parse' ? new Promise(resolve => resolveParse = resolve) : {...recipe,id:'saved'});
  const navigate = vi.fn(); render(Editor,{navigate}); await awaitBlankEditor();
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Soup'}}); await fireEvent.input(screen.getByLabelText('Paste or write your recipe'),{target:{value:'Original'}});
  await fireEvent.click(screen.getByRole('button',{name:'Organize'})); await waitFor(() => expect(resolveParse).toBeTypeOf('function'));
  await fireEvent.input(screen.getByLabelText('Paste or write your recipe'),{target:{value:'Changed while parsing'}});
  resolveParse({description:'Original',ingredient_groups:[],directions:'',notes:'',yield_amount:null,yield_unit:'',source_url:''});
  expect(await screen.findByText(/Result ignored/)).toBeInTheDocument(); expect(screen.getByLabelText('Paste or write your recipe')).toHaveValue('Changed while parsing');
  await fireEvent.click(screen.getByRole('button',{name:'Publish recipe'})); await waitFor(() => expect(navigate).toHaveBeenCalledWith('/recipes/saved'));
  const write = fetcher.mock.calls.find(([url]) => url === '/api/recipes'); expect(JSON.parse(write![1]!.body as string).mode).toBe('text'); expect(JSON.parse(write![1]!.body as string).source_text).toBe('Changed while parsing');
});
it('cancels parsing without losing source text', async () => {
  let signal: AbortSignal | undefined;
  mockApi((url,init) => url === '/api/session' ? identity : new Promise((_resolve,reject) => {signal = init?.signal as AbortSignal; signal?.addEventListener('abort',() => reject(new DOMException('Cancelled','AbortError')));}));
  render(Editor,{navigate:vi.fn()}); await awaitBlankEditor(); await fireEvent.input(await screen.findByLabelText('Paste or write your recipe'),{target:{value:'Keep every word'}}); await fireEvent.click(screen.getByRole('button',{name:'Organize'})); await waitFor(() => expect(signal).toBeDefined()); await fireEvent.click(screen.getByRole('button',{name:'Cancel organizing'})); expect(signal?.aborted).toBe(true); expect(screen.getByLabelText('Paste or write your recipe')).toHaveValue('Keep every word');
});
it('opens an in-editor comparison and preserves the true base with the draft', async () => {
  mockApi((url,init) => url === '/api/session' ? identity : init?.method === 'PUT' ? new Response(JSON.stringify({detail:'Newer revision exists'}),{status:409}) : recipe);
  const view = render(Editor,{recipeId:'r1',navigate:vi.fn()});
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'My <img src=x onerror=alert(1)> soup'}});
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  expect(await screen.findByRole('heading',{name:'Review recipe changes'})).toHaveFocus();
  expect(await screen.findByLabelText('Your changes to Title')).toHaveTextContent('-Soup');
  expect(screen.getByLabelText('Your changes to Title')).toHaveTextContent('+My <img src=x onerror=alert(1)> soup');
  expect(document.querySelector('img[src="x"]')).toBeNull();
  expect(screen.queryByLabelText('Recipe title')).not.toBeInTheDocument();
  view.unmount();
  const saved = JSON.parse(localStorage.getItem('notebook:draft:r1')!);
  expect(saved).toMatchObject({revision:2,draft:{title:'My <img src=x onerror=alert(1)> soup'},base:{revision:2,draft:{title:'Soup'}}});
});
it('scales only ingredients and exposes originals on the gram amount tooltip', async () => {
  const weighted = {...recipe,ingredient_groups:[{...recipe.ingredient_groups[0],ingredients:[{...recipe.ingredient_groups[0].ingredients[0],grams:{amount:120,estimated:false}}]}]};
  mockApi(url => url === '/api/session' ? identity : url.startsWith('/api/photos') ? {items:[]} : weighted);
  render(Detail,{recipeId:'r1',navigate:vi.fn()}); await screen.findByRole('heading',{name:'Soup'});
  expect(screen.queryByRole('button',{name:'2×'})).not.toBeInTheDocument();
  const controls = screen.getByRole('button',{name:'Adjust ingredient scale'});
  expect(controls).toHaveAttribute('aria-expanded','false');
  await fireEvent.click(controls); expect(controls).toHaveAttribute('aria-expanded','true');
  await fireEvent.click(screen.getByRole('tab',{name:'2×'}));
  expect(screen.getByText('1 cup stock')).toBeInTheDocument();
  expect(screen.getByText('Simmer 20 minutes at 180°C.')).toBeInTheDocument();
  expect(screen.queryByText('Original & weight details')).not.toBeInTheDocument();
  expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole('tab',{name:'Grams'}));
  const amount = screen.getByText('240 g');
  const trigger = amount.parentElement!;
  await fireEvent.mouseEnter(trigger);
  expect(await screen.findByRole('tooltip')).toHaveTextContent('As written: ½ cup stock');
  await fireEvent.mouseLeave(trigger); expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
  expect(screen.getByText('Simmer 20 minutes at 180°C.')).toBeInTheDocument();
  await fireEvent.click(screen.getByRole('tab',{name:'Original'}));
  expect(screen.getByText('1 cup stock')).toBeInTheDocument();
  expect(screen.queryByText('240 g')).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Save for later'}));
  await waitFor(() => expect(JSON.parse(localStorage.getItem('notebook:bookmarks')!)).toEqual(['r1']));
});
it('pairing requires a request and explicit destination confirmation before switching', async () => {
  const fetcher = mockApi(url => url === '/api/session' ? identity : url === '/api/devices' ? {devices:[]} : url.startsWith('/api/recipes?') ? {items:[],has_more:false} : {id:'pair',display_name:'Other cook',status:'approved',has_existing_profile:true});
  render(Profile,{token:'private-token',consumeToken:vi.fn()}); await screen.findByRole('heading',{name:'Use an existing profile'}); expect(fetcher.mock.calls.some(([url]) => String(url).includes('pairings'))).toBe(false);
  await fireEvent.click(screen.getByRole('button',{name:'Request connection'})); const complete = await screen.findByRole('button',{name:'Complete connection'}); expect(complete).toBeDisabled(); expect(screen.getByText(/will NOT merge/)).toBeInTheDocument(); await fireEvent.click(screen.getByLabelText('I want to switch this browser to this profile')); await waitFor(() => expect(complete).toBeEnabled()); await fireEvent.click(complete); await waitFor(() => expect(fetcher.mock.calls.some(([url,init]) => String(url).endsWith('/complete') && init?.body === JSON.stringify({switch_profile:true}))).toBe(true));
});
