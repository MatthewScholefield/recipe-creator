import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, expect, it, vi } from 'vitest';
import Editor from './Editor.svelte';
import { appState } from './app-state.svelte';
import { clearSession } from './api';
import { blank, changedIngredient, ingredient } from './recipe';
import { createDraft, listDrafts, readDraft, saveDraft } from './drafts';

const identity = {user:{id:'u1',display_name:'Cook',state:'active',photo_trusted:false},device_id:'d1',admin:false,csrf_token:'csrf'};
const anonymous = {...identity,user:null,device_id:null};
const oldRow = {...ingredient(),id:'legacy-row',original_text:'  ½ cup stock ',quantity:'0.5',unit:'cup',name:'stock',grams:{amount:120,estimated:true,basis:'legacy'}};
const recipe = {...blank('structured'),id:'r1',title:'Soup',revision:2,owner_id:'u1',author_name:'Cook',can_edit:true,directions:'  Simmer\n',ingredient_groups:[{id:'legacy-group',name:'',ingredients:[oldRow]}]};
function mockApi(handler: (url: string, init?: RequestInit) => unknown | Promise<unknown>, named = true) {
  const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const path = String(url);
    const body = path === '/api/session' ? (named ? identity : anonymous) : path === '/api/tags' ? {tags:['dinner','breakfast','asian'],classifier_tags:['breakfast','lunch','dinner','dessert']} : await handler(path, init);
    return body instanceof Response ? body : new Response(JSON.stringify(body));
  });
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
function localDraft(structured = false) {
  const value = createDraft('Soup'); value.draft = structured ? {...recipe} : {...blank(),title:'Soup',source_text:'  Exact source\n'}; saveDraft(value); return value;
}
function parseResult(source = '  Exact source\n') { return {source_hash:'hash',source_text:source,description:'',ingredient_groups:[{id:'g1',name:'',ingredients:[{...ingredient(),id:'i1',original_text:'2 eggs'}]}],directions:'  Exact source\n',notes:'',unclassified:'',warnings:[]}; }
function lineResult(body: string) {
  const {lines} = JSON.parse(body) as {lines:{id:string;text:string}[]};
  return {items:lines.map(line => ({...line,method:'deterministic',ingredient:{...changedIngredient(ingredient(),line.text),id:line.id,name:'eggs',quantity:'2'}})),warnings:[]};
}
beforeEach(() => { localStorage.clear(); clearSession(); appState.identity = null; vi.restoreAllMocks(); });

it('opens a nonblocking draft chooser without creating an empty draft and starts in text', async () => {
  mockApi(() => recipe); const first = localDraft(); localStorage.setItem('notebook:editor-mode', '"structured"');
  const navigate = vi.fn(); render(Editor,{navigate});
  await screen.findByRole('button',{name:'Start a new recipe'}); expect(listDrafts()).toHaveLength(1);
  expect(screen.getByRole('link',{name:'Resume Soup'})).toHaveAttribute('href',`/new?draft=${first.id}`);
  await fireEvent.click(screen.getByRole('button',{name:'Start a new recipe'}));
  expect(await screen.findByLabelText('Paste or write your recipe')).toHaveValue('');
  expect(listDrafts()).toHaveLength(2); await waitFor(() => expect(navigate).toHaveBeenCalledWith(expect.stringMatching(/^\/new\?draft=/),{replace:true}));
});
it('does not substitute another draft for a missing draft URL', async () => {
  mockApi(() => recipe); localDraft(); render(Editor,{draftId:crypto.randomUUID(),navigate:vi.fn()});
  expect(await screen.findByRole('alert')).toHaveTextContent('missing or unreadable');
  expect(screen.queryByLabelText('Recipe title')).not.toBeInTheDocument(); expect(listDrafts()).toHaveLength(1);
});
it('organizes anonymously in two guarded stages and shows the bottom name tooltip', async () => {
  const value = localDraft(); const fetcher = mockApi((url, init) => url === '/api/parse' ? parseResult() : lineResult(init!.body as string), false);
  render(Editor,{draftId:value.id,navigate:vi.fn()}); await screen.findByLabelText('Paste or write your recipe');
  const publish = screen.getByRole('button',{name:'Publish recipe'}); expect(publish).toBeDisabled();
  const wrapper = screen.getByRole('group',{name:'Set your name to publish'}); expect(wrapper).toHaveAttribute('tabindex','0');
  await fireEvent.focusIn(wrapper); expect(await screen.findByRole('tooltip')).toHaveTextContent('Set your name to publish');
  await fireEvent.click(screen.getByRole('button',{name:'Organize'}));
  expect(await screen.findByLabelText('Ingredient 1.1')).toHaveValue('2 eggs');
  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => String(url).includes('/ingredients/parse'))).toHaveLength(1));
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('/identity'))).toBe(false);
  expect(screen.getByRole('button',{name:'Publish recipe'})).toBeDisabled();
});
it('ignores whole-recipe results after typing and retains exact source on undo', async () => {
  const value = localDraft(); let finish!: (value: unknown) => void;
  mockApi(url => url === '/api/parse' ? new Promise(resolve => finish = resolve) : recipe);
  render(Editor,{draftId:value.id,navigate:vi.fn()}); await screen.findByLabelText('Paste or write your recipe');
  await fireEvent.click(screen.getByRole('button',{name:'Organize'})); await waitFor(() => expect(finish).toBeTypeOf('function'));
  expect(screen.getByRole('status',{name:'Organizing…'})).toBeInTheDocument();
  await fireEvent.input(screen.getByLabelText('Paste or write your recipe'),{target:{value:' changed\n'}}); finish(parseResult());
  await waitFor(() => expect(screen.getByLabelText('Paste or write your recipe')).toHaveValue(' changed\n'));
  expect(screen.queryByLabelText('Ingredient 1.1')).not.toBeInTheDocument();
});
it('single ingredient inputs preserve untouched metadata; Enter inserts and focuses, ignoring IME', async () => {
  const value = localDraft(true); mockApi(() => recipe); const view = render(Editor,{draftId:value.id,navigate:vi.fn()});
  const input = await screen.findByLabelText('Ingredient 1.1'); expect(input).toHaveValue(oldRow.original_text);
  expect(screen.queryByLabelText('Quantity')).not.toBeInTheDocument();
  await fireEvent.keyDown(input,{key:'Enter',isComposing:true}); expect(screen.queryByLabelText('Ingredient 1.2')).not.toBeInTheDocument();
  await fireEvent.keyDown(input,{key:'Enter'}); const next = await screen.findByLabelText('Ingredient 1.2'); expect(next).toHaveFocus();
  await fireEvent.input(next,{target:{value:'  2 eggs '}}); view.unmount();
  const stored = readDraft(value.id)!; expect(stored.draft.ingredient_groups[0].ingredients[0]).toEqual(oldRow);
  const changed = stored.draft.ingredient_groups[0].ingredients[1]; expect(changed).toMatchObject({original_text:'  2 eggs ',quantity:null,grams:null,name:''}); expect(stored.ingredientLines?.[changed.id]).toBe('  2 eggs ');
});
it('batches dirty lines at save, omits placeholders, and deletes only the successful draft', async () => {
  const value = localDraft(true), other = localDraft();
  const fetcher = mockApi((url, init) => url === '/api/ingredients/parse' ? lineResult(init!.body as string) : recipe);
  const navigate = vi.fn(); render(Editor,{draftId:value.id,navigate}); const input = await screen.findByLabelText('Ingredient 1.1');
  await fireEvent.input(input,{target:{value:'2 eggs'}}); await fireEvent.keyDown(input,{key:'Enter'});
  await waitFor(() => expect(screen.getByRole('button',{name:'Publish recipe'})).toBeEnabled());
  await fireEvent.click(screen.getByRole('button',{name:'Publish recipe'}));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith('/recipes/r1'));
  const parse = fetcher.mock.calls.filter(([url]) => url === '/api/ingredients/parse'); expect(parse).toHaveLength(1);
  expect(JSON.parse(parse[0][1]!.body as string)).toEqual({lines:[{id:oldRow.id,text:'2 eggs'}]});
  const write = fetcher.mock.calls.find(([url]) => url === '/api/recipes')!;
  const payload = JSON.parse(write[1]!.body as string); expect(payload.ingredient_groups[0].ingredients).toHaveLength(1);
  expect(payload.ingredient_groups[0].ingredients[0]).toMatchObject({id:oldRow.id,original_text:'2 eggs',quantity:'2',grams:null});
  expect(readDraft(value.id)).toBeNull(); expect(readDraft(other.id)).not.toBeNull();
});
it('keeps second-stage classification on failure and explicitly saves original ingredient text without another parse', async () => {
  const value = localDraft(); const fetcher = mockApi(url => url === '/api/parse' ? parseResult() : url === '/api/ingredients/parse' ? new Response(JSON.stringify({detail:'Quota exhausted'}),{status:429}) : recipe);
  const navigate = vi.fn(); render(Editor,{draftId:value.id,navigate}); await screen.findByLabelText('Paste or write your recipe');
  await fireEvent.click(screen.getByRole('button',{name:'Organize'}));
  const fallback = await screen.findByRole('button',{name:'Save with original ingredient text'});
  expect(screen.getByLabelText('Ingredient 1.1')).toHaveValue('2 eggs');
  await fireEvent.click(fallback); await waitFor(() => expect(navigate).toHaveBeenCalled());
  expect(fetcher.mock.calls.filter(([url]) => url === '/api/ingredients/parse')).toHaveLength(1);
  const write = fetcher.mock.calls.find(([url]) => url === '/api/recipes')!;
  expect(JSON.parse(write[1]!.body as string).ingredient_groups[0].ingredients[0]).toMatchObject({original_text:'2 eggs',quantity:null,grams:null,name:''});
});
it('ignores ingredient preview after reorder, cancel, and undo and preserves the original source', async () => {
  const value = localDraft(); let finish!: (value: unknown) => void; let body = '';
  mockApi((url, init) => url === '/api/parse' ? parseResult() : new Promise(resolve => {finish = resolve; body = init!.body as string;}));
  render(Editor,{draftId:value.id,navigate:vi.fn()}); await screen.findByLabelText('Paste or write your recipe');
  await fireEvent.click(screen.getByRole('button',{name:'Organize'})); await screen.findByLabelText('Ingredient 1.1');
  await waitFor(() => expect(finish).toBeTypeOf('function'));
  await fireEvent.click(screen.getByRole('button',{name:'Undo organization'})); finish(lineResult(body));
  expect(await screen.findByLabelText('Paste or write your recipe')).toHaveValue(value.draft.source_text); expect(screen.queryByLabelText('Ingredient 1.1')).not.toBeInTheDocument();
});
it('requires explicit tag creation and blocks conflicting classifiers without rewriting legacy tags', async () => {
  const value = localDraft(true); value.draft.tags = ['Dinner','BREAKFAST','asian']; saveDraft(value); mockApi(() => recipe);
  render(Editor,{draftId:value.id,navigate:vi.fn()}); expect(await screen.findByRole('alert')).toHaveTextContent('Choose only one meal type');
  expect(screen.getByRole('button',{name:'Publish recipe'})).toBeDisabled(); await fireEvent.click(screen.getByRole('button',{name:'Remove BREAKFAST'}));
  const picker = screen.getByRole('combobox',{name:'Tags'}); await fireEvent.input(picker,{target:{value:'New tag'}}); await fireEvent.blur(picker);
  expect(screen.queryByRole('button',{name:'Remove New tag'})).not.toBeInTheDocument();
  await fireEvent.focus(picker); await fireEvent.click(screen.getByRole('button',{name:'Create “New tag”'}));
  expect(screen.getByRole('button',{name:'Remove New tag'})).toBeInTheDocument(); expect(screen.getByRole('button',{name:'Publish recipe'})).toBeEnabled();
});
it('retains the same idempotency key across failed POST and retry', async () => {
  const value = localDraft(); let count = 0;
  const fetcher = mockApi(() => ++count === 1 ? new Response(JSON.stringify({detail:'Connection lost'}),{status:503}) : recipe);
  const navigate = vi.fn(); render(Editor,{draftId:value.id,navigate}); await screen.findByLabelText('Recipe title');
  await waitFor(() => expect(screen.getByRole('button',{name:'Publish recipe'})).toBeEnabled());
  await fireEvent.click(screen.getByRole('button',{name:'Publish recipe'})); expect(await screen.findByRole('alert')).toHaveTextContent('Connection lost'); expect(readDraft(value.id)?.publishKey).toBe(value.publishKey);
  await fireEvent.click(screen.getByRole('button',{name:'Publish recipe'})); await waitFor(() => expect(navigate).toHaveBeenCalled());
  const writes = fetcher.mock.calls.filter(([url]) => url === '/api/recipes'); expect(writes).toHaveLength(2);
  expect(writes.map(([,init]) => new Headers(init!.headers).get('Idempotency-Key'))).toEqual([value.publishKey,value.publishKey]);
});
it('pauses same-ID external autosave and forks without overwriting the other tab', async () => {
  const value = localDraft(); mockApi(() => recipe); const navigate = vi.fn(); render(Editor,{draftId:value.id,navigate});
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'My copy'}});
  const other = {...value,draft:{...value.draft,title:'Other tab'},updatedAt:new Date().toISOString()}; saveDraft(other);
  expect(await screen.findByRole('heading',{name:'Changed in another tab'})).toBeInTheDocument();
  await new Promise(resolve => setTimeout(resolve,400)); expect(readDraft(value.id)?.draft.title).toBe('Other tab');
  await fireEvent.click(screen.getByRole('button',{name:'Save as new draft'})); await waitFor(() => expect(navigate).toHaveBeenCalled());
  expect(listDrafts()).toHaveLength(2); expect(readDraft(value.id)?.draft.title).toBe('Other tab');
  expect(listDrafts().map(item => readDraft(item.id)?.draft.title)).toContain('My copy');
});
it('does not apply a late ingredient preview after reorder or deletion', async () => {
  const value = localDraft(true); value.draft.ingredient_groups[0].ingredients.push({...ingredient(),id:'second',original_text:'salt'}); saveDraft(value);
  let finish!: (value: unknown) => void, body = '';
  mockApi((_url, init) => new Promise(resolve => { finish = resolve; body = init!.body as string; }));
  render(Editor,{draftId:value.id,navigate:vi.fn()});
  await fireEvent.input(await screen.findByLabelText('Ingredient 1.1'),{target:{value:'2 eggs'}});
  await fireEvent.click(screen.getByRole('button',{name:'Organize ingredients'})); await waitFor(() => expect(finish).toBeTypeOf('function'));
  await fireEvent.click(screen.getByRole('button',{name:'Move ingredient 1.1 down'}));
  await fireEvent.click(screen.getByRole('button',{name:'Remove ingredient 1.2'})); finish(lineResult(body));
  expect(screen.getByLabelText('Ingredient 1.1')).toHaveValue('salt'); expect(screen.queryByLabelText('Ingredient 1.2')).not.toBeInTheDocument();
});
it('locks edits during a save and ignores responses after teardown while retaining the draft', async () => {
  const value = localDraft(); let finish!: (value: unknown) => void;
  mockApi(() => new Promise(resolve => finish = resolve)); const navigate = vi.fn();
  const view = render(Editor,{draftId:value.id,navigate}); await screen.findByLabelText('Recipe title');
  await waitFor(() => expect(screen.getByRole('button',{name:'Publish recipe'})).toBeEnabled());
  await fireEvent.click(screen.getByRole('button',{name:'Publish recipe'})); await waitFor(() => expect(finish).toBeTypeOf('function'));
  expect(screen.getByLabelText('Recipe title')).toBeDisabled(); view.unmount(); finish(recipe);
  await new Promise(resolve => setTimeout(resolve,20)); expect(navigate).not.toHaveBeenCalled(); expect(readDraft(value.id)?.publishKey).toBe(value.publishKey);
});
it('shows the bottom name prompt after an expired-session save and keeps the draft', async () => {
  const value = localDraft(); let expired = false;
  vi.stubGlobal('fetch',vi.fn(async (url: string) => {
    if (url === '/api/session') return new Response(JSON.stringify(expired ? anonymous : identity));
    if (url === '/api/tags') return new Response(JSON.stringify({tags:[],classifier_tags:[]}));
    expired = true; return new Response(JSON.stringify({detail:'Set your name'}),{status:401});
  }));
  render(Editor,{draftId:value.id,navigate:vi.fn()}); await screen.findByLabelText('Recipe title');
  await waitFor(() => expect(screen.getByRole('button',{name:'Publish recipe'})).toBeEnabled());
  await fireEvent.click(screen.getByRole('button',{name:'Publish recipe'}));
  expect(await screen.findByRole('group',{name:'Set your name to publish'})).toHaveAttribute('tabindex','0');
  expect(screen.getByRole('button',{name:'Publish recipe'})).toBeDisabled(); expect(readDraft(value.id)?.draft.source_text).toBe(value.draft.source_text);
});
it('keeps an in-memory editor open if starting a draft cannot persist', async () => {
  mockApi(() => recipe); vi.spyOn(Storage.prototype,'setItem').mockImplementation(() => {throw new Error('quota');});
  const navigate = vi.fn(); render(Editor,{navigate}); await fireEvent.click(await screen.findByRole('button',{name:'Start a new recipe'}));
  expect(await screen.findByLabelText('Paste or write your recipe')).toBeInTheDocument();
  expect(screen.getByText(/Local storage is unavailable/)).toBeInTheDocument(); expect(navigate).not.toHaveBeenCalled();
});
it('requires a choice before formatting source and can undo back to exact original text', async () => {
  const value = localDraft(true); value.draft.source_text = '  Original source\n'; saveDraft(value); mockApi(() => recipe);
  render(Editor,{draftId:value.id,navigate:vi.fn()}); await fireEvent.click(await screen.findByRole('button',{name:'Edit as text'}));
  expect(screen.queryByLabelText('Paste or write your recipe')).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Use formatted current recipe'}));
  expect(screen.getByLabelText('Paste or write your recipe')).not.toHaveValue(value.draft.source_text);
  await fireEvent.click(screen.getByRole('button',{name:'Undo organization'}));
  await fireEvent.click(screen.getByRole('button',{name:'Edit as text'})); await fireEvent.click(screen.getByRole('button',{name:'Use original text'}));
  expect(screen.getByLabelText('Paste or write your recipe')).toHaveValue(value.draft.source_text);
});
it('retains recovered edits at their original revision on conflict', async () => {
  localStorage.setItem('notebook:draft:r1',JSON.stringify({draft:{...blank(),title:'Recovered',source_text:' exact\n'},revision:1,key:'key',saved:new Date().toISOString()}));
  const fetcher = mockApi((url, init) => init?.method === 'PUT' ? new Response(JSON.stringify({detail:'Newer revision'}),{status:409}) : recipe);
  const navigate = vi.fn(), view = render(Editor,{recipeId:'r1',navigate}); await fireEvent.click(await screen.findByRole('button',{name:'Recover draft'}));
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'})); expect(await screen.findByRole('heading',{name:'A newer version exists'})).toBeInTheDocument();
  const write = fetcher.mock.calls.find(([,init]) => init?.method === 'PUT')!; expect(JSON.parse(write[1]!.body as string).expected_revision).toBe(1);
  view.unmount(); expect(JSON.parse(localStorage.getItem('notebook:draft:r1')!).draft.source_text).toBe(' exact\n'); expect(navigate).not.toHaveBeenCalled();
});
