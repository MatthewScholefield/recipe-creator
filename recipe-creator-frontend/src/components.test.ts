import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, expect, it, vi } from 'vitest';
import { appState, DEFAULT_SITE_COPY, setSiteCopy } from './app-state.svelte';
beforeEach(() => {appState.identity = null; setSiteCopy({...DEFAULT_SITE_COPY});});
import Editor from './Editor.svelte';
import Browse from './Browse.svelte';
import Detail from './Detail.svelte';
import Profile from './Profile.svelte';
import Admin from './Admin.svelte';
import Photos from './Photos.svelte';
import { blank, ingredient } from './recipe';
const identity = {user:{id:'u1',display_name:'Cook',state:'active',photo_trusted:false},device_id:'d1',admin:false,csrf_token:'csrf'};
function mockApi(handler: (url: string, init?: RequestInit) => unknown | Promise<unknown>) {const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {const body = await handler(String(url),init); return body instanceof Response ? body : new Response(JSON.stringify(body));}); vi.stubGlobal('fetch',fetcher); return fetcher;}
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
  expect(await screen.findByRole('link',{name:'Soup'})).toBeInTheDocument(); expect(fetcher.mock.calls.some(([url]) => url === '/api/recipes?owner_id=u1&offset=0&limit=100')).toBe(true);
});
it('does not navigate after a publishing editor is unmounted', async () => {
  let finish!: (value: unknown) => void;
  mockApi(url => url === '/api/session' ? identity : new Promise(resolve => finish = resolve));
  const navigate = vi.fn(); const view = render(Editor,{navigate});
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Soup'}}); await fireEvent.click(screen.getByRole('button',{name:'Publish recipe'}));
  await waitFor(() => expect(finish).toBeTypeOf('function')); view.unmount(); finish(recipe); await new Promise(resolve => setTimeout(resolve,20)); expect(navigate).not.toHaveBeenCalled();
  expect(JSON.parse(localStorage.getItem('notebook:draft:new')!).draft.title).toBe('Soup');
});
it('rejects parse output that echoes a different source', async () => {
  mockApi(url => url === '/api/session' ? identity : {source_text:'Different',source_hash:'hash',description:'Changed',ingredient_groups:[],directions:'',notes:'',unclassified:'',warnings:[]});
  render(Editor,{navigate:vi.fn()}); await fireEvent.input(await screen.findByLabelText('Recipe body'),{target:{value:'  Exact source\n'}}); await fireEvent.click(screen.getByRole('button',{name:'Switch to Structured'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('different source'); expect(screen.getByLabelText('Recipe body')).toHaveValue('  Exact source\n');
});
it('recovers exact draft text and original revision before editing', async () => {
  localStorage.setItem('notebook:draft:r1',JSON.stringify({draft:{...blank(),title:'Recovered soup',source_text:'  exact draft\n\n'},revision:1,key:'retry-key',saved:new Date().toISOString()}));
  const fetcher = mockApi((url,init) => url === '/api/session' ? identity : recipe);
  render(Editor,{recipeId:'r1',navigate:vi.fn()}); await fireEvent.click(await screen.findByRole('button',{name:'Recover draft'})); expect(screen.getByLabelText('Recipe body')).toHaveValue('  exact draft\n\n'); await fireEvent.click(screen.getByRole('button',{name:'Save changes'})); await waitFor(() => expect(fetcher.mock.calls.some(([,init]) => init?.method === 'PUT')).toBe(true)); const write = fetcher.mock.calls.find(([,init]) => init?.method === 'PUT'); expect(JSON.parse(write![1]!.body as string).expected_revision).toBe(1);
});
it('keeps text publishable during a parser outage', async () => {
  mockApi(url => url === '/api/session' ? identity : new Response(JSON.stringify({detail:'AI unavailable'}),{status:503})); render(Editor,{navigate:vi.fn()}); await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Dinner'}}); await fireEvent.input(screen.getByLabelText('Recipe body'),{target:{value:'Original recipe'}}); await fireEvent.click(screen.getByRole('button',{name:'Switch to Structured'})); expect(await screen.findByRole('alert')).toHaveTextContent('Your text is safe'); expect(screen.getByLabelText('Recipe body')).toHaveValue('Original recipe'); expect(screen.getByRole('button',{name:'Publish recipe'})).toBeEnabled();
});
it('rejects unsupported photo files without loading a compressor or uploading', async () => {
  const fetcher = mockApi(url => url === '/api/session' ? identity : {items:[]}); render(Photos,{recipeId:'r1'}); const input = await screen.findByLabelText('Choose photo'); await fireEvent.change(input,{target:{files:[new File(['bad'],'camera.heic',{type:'image/heic'})]}}); expect(await screen.findByRole('alert')).toHaveTextContent('HEIC'); expect(fetcher.mock.calls.some(([,init]) => init?.method === 'POST')).toBe(false);
});
it('discards a merge preview if profile IDs change before it arrives', async () => {
  let resolvePreview!: (value: unknown) => void;
  mockApi(url => url === '/api/session' ? {...identity,admin:true} : url.startsWith('/api/admin/merge/preview?') ? new Promise(resolve => resolvePreview = resolve) : {photos:[]});
  render(Admin); await fireEvent.click(await screen.findByRole('button',{name:'merge'})); const source = screen.getByLabelText('Source profile ID (will merge into target)'); const target = screen.getByLabelText('Target profile ID (survives)'); await fireEvent.input(source,{target:{value:'source'}}); await fireEvent.input(target,{target:{value:'target'}}); await fireEvent.click(screen.getByRole('button',{name:'Preview merge'})); await waitFor(() => expect(resolvePreview).toBeTypeOf('function')); await fireEvent.input(target,{target:{value:'different'}}); resolvePreview({counts:{recipes:3}}); await waitFor(() => expect(screen.getByRole('button',{name:'Preview merge'})).toBeEnabled()); expect(screen.queryByRole('button',{name:'Confirm profile merge'})).not.toBeInTheDocument();
});
it('browses summaries without a reactive request loop or identity creation', async () => {
  const fetcher = mockApi(url => url.includes('/tags') ? {tags:['dinner']} : {items:[{id:'r1',title:'Soup',description:'Warm',tags:['dinner'],author_name:null}],has_more:false,errors:[]});
  render(Browse,{query:''}); expect(await screen.findByRole('link',{name:'Soup'})).toBeInTheDocument(); await new Promise(resolve => setTimeout(resolve,30)); expect(fetcher).toHaveBeenCalledTimes(2); expect(fetcher.mock.calls.some(([url]) => String(url).includes('/session'))).toBe(false);
});
it('ignores a stale parse after typing and allows a text-only publish', async () => {
  let resolveParse!: (value: unknown) => void;
  const fetcher = mockApi(url => url === '/api/session' ? identity : url === '/api/parse' ? new Promise(resolve => resolveParse = resolve) : {...recipe,id:'saved'});
  const navigate = vi.fn(); render(Editor,{navigate});
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Soup'}}); await fireEvent.input(screen.getByLabelText('Recipe body'),{target:{value:'Original'}});
  await fireEvent.click(screen.getByRole('button',{name:'Switch to Structured'})); await waitFor(() => expect(resolveParse).toBeTypeOf('function'));
  await fireEvent.input(screen.getByLabelText('Recipe body'),{target:{value:'Changed while parsing'}});
  resolveParse({source_hash:'hash',description:'Original',ingredient_groups:[],directions:'',notes:'',unclassified:'',warnings:[]});
  expect(await screen.findByText(/Result ignored/)).toBeInTheDocument(); expect(screen.getByLabelText('Recipe body')).toHaveValue('Changed while parsing');
  await fireEvent.click(screen.getByRole('button',{name:'Publish recipe'})); await waitFor(() => expect(navigate).toHaveBeenCalledWith('/recipes/saved'));
  const write = fetcher.mock.calls.find(([url]) => url === '/api/recipes'); expect(JSON.parse(write![1]!.body as string).mode).toBe('text'); expect(JSON.parse(write![1]!.body as string).source_text).toBe('Changed while parsing');
});
it('cancels parsing without losing source text', async () => {
  let signal: AbortSignal | undefined;
  mockApi((url,init) => url === '/api/session' ? identity : new Promise((_resolve,reject) => {signal = init?.signal as AbortSignal; signal?.addEventListener('abort',() => reject(new DOMException('Cancelled','AbortError')));}));
  render(Editor,{navigate:vi.fn()}); await fireEvent.input(await screen.findByLabelText('Recipe body'),{target:{value:'Keep every word'}}); await fireEvent.click(screen.getByRole('button',{name:'Switch to Structured'})); await waitFor(() => expect(signal).toBeDefined()); await fireEvent.click(screen.getByRole('button',{name:'Cancel organizing'})); expect(signal?.aborted).toBe(true); expect(screen.getByLabelText('Recipe body')).toHaveValue('Keep every word');
});
it('retains drafts and reports a revision conflict instead of overwriting', async () => {
  mockApi((url,init) => url === '/api/session' ? identity : init?.method === 'PUT' ? new Response(JSON.stringify({detail:'Newer revision exists'}),{status:409}) : recipe);
  const view = render(Editor,{recipeId:'r1',navigate:vi.fn()}); await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'My soup'}}); await fireEvent.click(screen.getByRole('button',{name:'Save changes'})); expect(await screen.findByRole('heading',{name:'A newer version exists'})).toBeInTheDocument(); expect(screen.getByLabelText('Recipe title')).toHaveValue('My soup'); view.unmount(); const saved = JSON.parse(localStorage.getItem('notebook:draft:r1')!); expect(saved.revision).toBe(2); expect(saved.draft.title).toBe('My soup');
});
it('scales only ingredients and exposes originals with native keyboard disclosure', async () => {
  mockApi(() => recipe); render(Detail,{recipeId:'r1',navigate:vi.fn()}); await screen.findByRole('heading',{name:'Soup'}); await fireEvent.click(screen.getByRole('button',{name:'2×'})); expect(screen.getByText('1 cup stock')).toBeInTheDocument(); expect(screen.getByText('Simmer 20 minutes at 180°C.')).toBeInTheDocument(); expect(screen.getByText('Original & weight details').tagName).toBe('SUMMARY'); await fireEvent.click(screen.getByRole('button',{name:'Save for later'})); await waitFor(() => expect(JSON.parse(localStorage.getItem('notebook:bookmarks')!)).toEqual(['r1']));
});
it('pairing requires a request and explicit destination confirmation before switching', async () => {
  const fetcher = mockApi(url => url === '/api/session' ? identity : url === '/api/devices' ? {devices:[]} : url.startsWith('/api/recipes?') ? {items:[],has_more:false} : {id:'pair',display_name:'Other cook',status:'approved',has_existing_profile:true});
  render(Profile,{token:'private-token',consumeToken:vi.fn()}); await screen.findByRole('heading',{name:'Use an existing profile'}); expect(fetcher.mock.calls.some(([url]) => String(url).includes('pairings'))).toBe(false);
  await fireEvent.click(screen.getByRole('button',{name:'Request connection'})); const complete = await screen.findByRole('button',{name:'Complete connection'}); expect(complete).toBeDisabled(); expect(screen.getByText(/will NOT merge/)).toBeInTheDocument(); await fireEvent.click(screen.getByLabelText('I want to switch this browser to this profile')); await waitFor(() => expect(complete).toBeEnabled()); await fireEvent.click(complete); await waitFor(() => expect(fetcher.mock.calls.some(([url,init]) => String(url).endsWith('/complete') && init?.body === JSON.stringify({switch_profile:true}))).toBe(true));
});
