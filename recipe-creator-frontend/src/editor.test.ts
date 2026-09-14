import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { beforeEach, expect, it, vi } from 'vitest';
import Editor from './Editor.svelte';
import { appState } from './app-state.svelte';
import { clearSession } from './api';
import { blank, changedIngredient, ingredient, recipeDraftFromRecipe } from './recipe';
import { createDraft, listDrafts, readDraft, saveDraft } from './drafts';

const identity = {user:{id:'u1',display_name:'Cook',state:'active',photo_trusted:false},device_id:'d1',admin:false,csrf_token:'csrf'};
const anonymous = {...identity,user:null,device_id:null};
const oldRow = {...ingredient(),id:'legacy-row',original_text:'  ½ cup stock ',quantity:'0.5',unit:'cup',name:'stock',grams:{amount:120,estimated:true,basis:'legacy'}};
const recipe = {...blank('structured'),id:'r1',title:'Soup',revision:2,owner_id:'u1',author_name:'Cook',can_edit:true,enrichment_status:'complete',directions:'  Simmer\n',ingredient_groups:[{id:'legacy-group',name:'',ingredients:[oldRow]}]};
function mockApi(handler: (url: string, init?: RequestInit) => unknown | Promise<unknown>, named = true, currentIdentity = identity) {
  const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const path = String(url);
    const body = path === '/api/session' ? (named ? currentIdentity : anonymous) : path === '/api/tags' ? {tags:['dinner','breakfast','asian'],classifier_tags:['breakfast','lunch','dinner','dessert']} : await handler(path, init);
    return body instanceof Response ? body : new Response(JSON.stringify(body));
  });
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
function localDraft(structured = false) {
  const value = createDraft('Soup'); value.draft = structured ? {...recipe} : {...blank(),title:'Soup',source_text:'  Exact source\n'}; saveDraft(value); return value;
}
function parseResult() { return {description:'',ingredient_groups:[{id:'g1',name:'',ingredients:[{...ingredient(),id:'i1',original_text:'2 eggs',quantity:'2',name:'eggs'}]}],directions:'  Exact source\n',notes:'',yield_amount:'4',yield_unit:'servings',source_url:'https://example.com'}; }
function lineResult(body: string) {
  const {lines} = JSON.parse(body) as {lines:{id:string;text:string}[]};
  return {items:lines.map(line => ({...line,method:'llm',ingredient:{...changedIngredient(ingredient(),line.text),id:line.id,name:'eggs',quantity:'2'}}))};
}
beforeEach(() => { localStorage.clear(); clearSession(); appState.identity = null; vi.restoreAllMocks(); });
it('puts the searchable author field at the bottom of existing recipe edits for admins', async () => {
  const adminIdentity = {...identity,admin:true};
  mockApi(url => url === '/api/recipes/r1' ? recipe : {items:[],has_more:false}, true, adminIdentity);
  render(Editor,{recipeId:'r1',navigate:vi.fn()});
  const title = await screen.findByLabelText('Recipe title');
  const author = screen.getByRole('combobox',{name:'Author'});
  expect(title.compareDocumentPosition(author) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(author).toHaveValue('Cook');
  expect(screen.queryByText('Change author')).not.toBeInTheDocument();
});
it('discards an in-progress edit beside the save action without recreating its draft', async () => {
  mockApi(() => recipe);
  const navigate = vi.fn();
  const view = render(Editor,{recipeId:'r1',navigate});
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Changed locally'}});
  await waitFor(() => expect(localStorage.getItem('notebook:draft:r1')).not.toBeNull());
  const saveButton = screen.getByRole('button',{name:'Save changes'});
  const discardButton = screen.getByRole('button',{name:'Discard changes'});
  expect(saveButton).toBeEnabled();
  await fireEvent.click(discardButton);
  expect(screen.getByRole('heading',{name:'Discard your changes?'})).toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Confirm discard'}));
  expect(localStorage.getItem('notebook:draft:r1')).toBeNull();
  expect(navigate).toHaveBeenCalledWith('/recipes/r1');
  view.unmount();
  expect(localStorage.getItem('notebook:draft:r1')).toBeNull();
});


it('opens a nonblocking draft chooser without creating an empty draft and starts in text', async () => {
  mockApi(() => recipe); const first = localDraft(); localStorage.setItem('notebook:editor-mode', '"structured"');
  const navigate = vi.fn(); const view = render(Editor,{navigate});
  await screen.findByRole('button',{name:'Start a new recipe'}); expect(listDrafts()).toHaveLength(1);
  expect(screen.getByRole('link',{name:'Resume Soup'})).toHaveAttribute('href',`/new?draft=${first.id}`);
  await fireEvent.click(screen.getByRole('button',{name:'Start a new recipe'}));
  expect(await screen.findByLabelText('Paste or write your recipe')).toHaveValue('');
  view.unmount(); expect(listDrafts()).toHaveLength(1); expect(navigate).not.toHaveBeenCalled();
});
it('does not substitute another draft for a missing draft URL', async () => {
  mockApi(() => recipe); localDraft(); render(Editor,{draftId:crypto.randomUUID(),navigate:vi.fn()});
  expect(await screen.findByRole('alert')).toHaveTextContent('missing or unreadable');
  expect(screen.queryByLabelText('Recipe title')).not.toBeInTheDocument(); expect(listDrafts()).toHaveLength(1);
});
it('organizes anonymously from the direct recipe result and shows the bottom name tooltip', async () => {
  const value = localDraft(); const fetcher = mockApi(url => url === '/api/parse' ? parseResult() : recipe, false);
  render(Editor,{draftId:value.id,navigate:vi.fn()}); await screen.findByLabelText('Paste or write your recipe');
  const publish = screen.getByRole('button',{name:'Publish recipe'}); expect(publish).toBeDisabled();
  const wrapper = screen.getByRole('group',{name:'Set your name to publish'}); expect(wrapper).toHaveAttribute('tabindex','0');
  await fireEvent.focusIn(wrapper); expect(await screen.findByRole('tooltip')).toHaveTextContent('Set your name to publish');
  await fireEvent.click(screen.getByRole('button',{name:'Organize'}));
  expect(await screen.findByLabelText('Ingredient 1.1')).toHaveValue('2 eggs');
  expect(screen.getByLabelText('Yield amount')).toHaveValue('4');
  expect(screen.getByLabelText('Source link')).toHaveValue('https://example.com');
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('/ingredients/parse'))).toBe(false);
  expect(screen.getByRole('button',{name:'Publish recipe'})).toBeDisabled();
});
it('disables the editor and shows progress beside the action while organizing a recipe', async () => {
  const value = localDraft(); const pending = (Promise as PromiseConstructor & {withResolvers<T>(): {promise: Promise<T>; resolve(value: T): void; reject(reason?: unknown): void}}).withResolvers<unknown>();
  mockApi(url => url === '/api/parse' ? pending.promise : recipe);
  render(Editor,{draftId:value.id,navigate:vi.fn()}); const source = await screen.findByLabelText('Paste or write your recipe');
  const organize = screen.getByRole('button',{name:'Organize'}); await fireEvent.click(organize);
  const progress = screen.getByRole('status',{name:'Organizing…'});
  expect(progress.closest('.toolbar')).toContainElement(organize); expect(source).toBeDisabled(); expect(screen.getByLabelText('Recipe title')).toBeDisabled();
  expect(source.closest('fieldset')).toHaveAttribute('aria-busy','true');
  pending.resolve(parseResult()); expect(await screen.findByLabelText('Ingredient 1.1')).toHaveValue('2 eggs');
  expect(screen.getByLabelText('Recipe title')).toBeEnabled();
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
it('undoes direct recipe organization and preserves the original source', async () => {
  const value = localDraft();
  mockApi(url => url === '/api/parse' ? parseResult() : recipe);
  render(Editor,{draftId:value.id,navigate:vi.fn()}); await screen.findByLabelText('Paste or write your recipe');
  await fireEvent.click(screen.getByRole('button',{name:'Organize'})); await screen.findByLabelText('Ingredient 1.1');
  await fireEvent.click(screen.getByRole('button',{name:'Undo organization'}));
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
it('disables structured edits and shows progress beside the action while organizing ingredients', async () => {
  const value = localDraft(true); const pending = (Promise as PromiseConstructor & {withResolvers<T>(): {promise: Promise<T>; resolve(value: T): void; reject(reason?: unknown): void}}).withResolvers<unknown>(); let body = '';
  mockApi((_url, init) => { body = init!.body as string; return pending.promise; });
  render(Editor,{draftId:value.id,navigate:vi.fn()});
  const input = await screen.findByLabelText('Ingredient 1.1'); await fireEvent.input(input,{target:{value:'2 eggs'}});
  const organize = screen.getByRole('button',{name:'Organize ingredients'}); await fireEvent.click(organize);
  const progress = screen.getByRole('status',{name:'Organizing ingredients…'});
  expect(progress.closest('.toolbar')).toContainElement(organize); expect(input).toBeDisabled();
  expect(screen.getByRole('button',{name:'Move ingredient 1.1 up'})).toBeDisabled();
  pending.resolve(lineResult(body)); await waitFor(() => expect(input).toBeEnabled()); expect(input).toHaveValue('2 eggs');
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
  expect(screen.queryByText(/Local storage is unavailable/)).not.toBeInTheDocument();
  await fireEvent.input(screen.getByLabelText('Recipe title'),{target:{value:'Soup'}});
  expect(await screen.findByText(/Local storage is unavailable/)).toBeInTheDocument(); expect(navigate).not.toHaveBeenCalled();
});
it('formats structured recipes as Markdown text and can undo organization', async () => {
  const value = localDraft(true); value.draft.source_text = '  Original source\n'; saveDraft(value); mockApi(() => recipe);
  render(Editor,{draftId:value.id,navigate:vi.fn()}); await fireEvent.click(await screen.findByRole('button',{name:'Edit as text'}));
  expect(screen.getByLabelText('Paste or write your recipe')).toHaveValue(expect.stringContaining('## Ingredients'));
  expect(screen.getByLabelText('Paste or write your recipe')).not.toHaveValue(value.draft.source_text);
  await fireEvent.click(screen.getByRole('button',{name:'Undo organization'}));
  expect(screen.queryByLabelText('Paste or write your recipe')).not.toBeInTheDocument();
});
it('keeps old recovered drafts and labels their comparison as unattributed', async () => {
  localStorage.setItem('notebook:draft:r1',JSON.stringify({draft:{...blank(),title:'Recovered',source_text:' exact\n'},revision:1,key:'key',saved:new Date().toISOString()}));
  const fetcher = mockApi((_url, init) => init?.method === 'PUT' ? new Response(JSON.stringify({detail:'Newer revision'}),{status:409}) : recipe);
  const navigate = vi.fn(), view = render(Editor,{recipeId:'r1',navigate});
  await fireEvent.click(await screen.findByRole('button',{name:'Edit draft'}));
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  expect(await screen.findByRole('heading',{name:'Review recipe changes'})).toBeInTheDocument();
  expect(await screen.findByText(/does not include its starting recipe/)).toBeInTheDocument();
  expect(screen.getByRole('heading',{name:'Latest saved recipe → your draft'})).toBeInTheDocument();
  const write = fetcher.mock.calls.find(([,init]) => init?.method === 'PUT')!;
  expect(JSON.parse(write[1]!.body as string).expected_revision).toBe(1);
  view.unmount();
  expect(JSON.parse(localStorage.getItem('notebook:draft:r1')!).draft.source_text).toBe(' exact\n');
  expect(navigate).not.toHaveBeenCalled();
});
it('keeps the historical base through reload and replaces exactly the reviewed revision', async () => {
  const base = {...recipe,revision:2,directions:'Simmer 20 minutes',notes:''};
  const candidate = {...recipeDraftFromRecipe(base),directions:'Simmer 30 minutes',notes:''};
  localStorage.setItem('notebook:draft:r1',JSON.stringify({draft:candidate,revision:2,base:{revision:2,draft:recipeDraftFromRecipe(base)},key:'key',saved:new Date().toISOString()}));
  let live = {...base,revision:3,directions:'Simmer 25 minutes',notes:'Saved-only note'};
  const payloads: Record<string, unknown>[] = [];
  const fetcher = mockApi((_url, init) => {
    if (init?.method === 'PUT') {
      const payload = JSON.parse(init.body as string); payloads.push(payload);
      if (payloads.length === 1) return new Response(JSON.stringify({detail:'Newer revision'}),{status:409});
      live = {...live,...payload,revision:4}; return live;
    }
    return live;
  });
  const navigate = vi.fn();
  render(Editor,{recipeId:'r1',navigate});
  await fireEvent.click(await screen.findByRole('button',{name:'Edit draft'}));
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  await screen.findByText(/Latest saved recipe: revision 3/);
  expect(screen.getByLabelText('Your changes to Directions')).toHaveTextContent('-Simmer 20 minutes');
  expect(screen.getByLabelText('Your changes to Directions')).toHaveTextContent('+Simmer 30 minutes');
  expect(screen.getByLabelText('Saved changes to Directions')).toHaveTextContent('+Simmer 25 minutes');
  expect(screen.getByLabelText('Saved changes to Notes')).toHaveTextContent('+Saved-only note');
  await fireEvent.click(screen.getByRole('button',{name:'Save my version'}));
  expect(screen.getByText(/Saved changes not present in your draft will be lost/)).toBeInTheDocument();
  await fireEvent.click(screen.getByRole('button',{name:'Replace recipe'}));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith('/recipes/r1'));
  expect(payloads.map(payload => payload.expected_revision)).toEqual([2,3]);
  expect(payloads[1]).toMatchObject({directions:'Simmer 30 minutes',notes:''});
  expect(fetcher.mock.calls.filter(([url]) => url === '/api/ingredients/parse')).toHaveLength(0);
  expect(localStorage.getItem('notebook:draft:r1')).toBeNull();
});
it('refreshes repeated conflicts and requires a new confirmation without changing the candidate or base', async () => {
  const base = {...recipe,revision:2,directions:'Simmer 20 minutes',notes:''};
  let live = base, puts = 0;
  const payloads: Record<string, unknown>[] = [];
  const fetcher = mockApi((_url, init) => {
    if (init?.method === 'PUT') {
      const payload = JSON.parse(init.body as string); payloads.push(payload); puts += 1;
      if (puts === 1) { live = {...base,revision:3,directions:'Simmer 25 minutes'}; return new Response('{}',{status:409}); }
      if (puts === 2) { live = {...base,revision:4,directions:'Simmer 27 minutes',notes:'New saved note'}; return new Response('{}',{status:409}); }
      live = {...live,...payload,revision:5}; return live;
    }
    return live;
  });
  const navigate = vi.fn();
  render(Editor,{recipeId:'r1',navigate});
  await fireEvent.input(await screen.findByLabelText('Directions'),{target:{value:'Simmer 30 minutes'}});
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  await screen.findByText(/Latest saved recipe: revision 3/);
  await fireEvent.click(screen.getByRole('button',{name:'Back to editing'}));
  expect(await screen.findByRole('button',{name:'Review changes'})).toHaveFocus();
  await fireEvent.input(screen.getByLabelText('Directions'),{target:{value:'Simmer 35 minutes'}});
  await fireEvent.click(screen.getByRole('button',{name:'Review changes'}));
  await screen.findByText(/Latest saved recipe: revision 3/);
  expect(puts).toBe(1);
  expect(screen.getByLabelText('Your changes to Directions')).toHaveTextContent('+Simmer 35 minutes');
  await fireEvent.click(screen.getByRole('button',{name:'Save my version'}));
  await fireEvent.click(screen.getByRole('button',{name:'Replace recipe'}));
  expect(await screen.findByText(/recipe changed again/)).toBeInTheDocument();
  await screen.findByText(/Latest saved recipe: revision 4/);
  expect(puts).toBe(2);
  expect(screen.queryByRole('button',{name:'Replace recipe'})).not.toBeInTheDocument();
  expect(screen.getByLabelText('Your changes to Directions')).toHaveTextContent('+Simmer 35 minutes');
  expect(screen.getByLabelText('Saved changes to Notes')).toHaveTextContent('+New saved note');
  await fireEvent.click(screen.getByRole('button',{name:'Save my version'}));
  await fireEvent.click(screen.getByRole('button',{name:'Replace recipe'}));
  await waitFor(() => expect(navigate).toHaveBeenCalledWith('/recipes/r1'));
  expect(payloads.map(payload => payload.expected_revision)).toEqual([2,3,4]);
  expect(payloads.map(payload => payload.directions)).toEqual(['Simmer 30 minutes','Simmer 35 minutes','Simmer 35 minutes']);
  expect(fetcher.mock.calls.filter(([,init]) => init?.method === 'PUT')).toHaveLength(3);
});
it('discards only the local edit after confirmation and does not recreate it', async () => {
  let live = recipe;
  const fetcher = mockApi((_url, init) => {
    if (init?.method === 'PUT') { live = {...recipe,revision:3,title:'Saved elsewhere'}; return new Response('{}',{status:409}); }
    return live;
  });
  const navigate = vi.fn(), view = render(Editor,{recipeId:'r1',navigate});
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Discard locally'}});
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  await screen.findByRole('heading',{name:'Review recipe changes'});
  await fireEvent.click(screen.getByRole('button',{name:'Discard my draft'}));
  await fireEvent.click(screen.getByRole('button',{name:'Keep reviewing'}));
  expect(localStorage.getItem('notebook:draft:r1')).not.toBeNull();
  expect(navigate).not.toHaveBeenCalled();
  await fireEvent.click(screen.getByRole('button',{name:'Discard my draft'}));
  await fireEvent.click(screen.getByRole('button',{name:'Discard draft'}));
  expect(navigate).toHaveBeenCalledWith('/recipes/r1');
  await new Promise(resolve => setTimeout(resolve,400));
  view.unmount();
  expect(localStorage.getItem('notebook:draft:r1')).toBeNull();
});
it('never resubmits after a saved recipe needs local draft cleanup retry', async () => {
  let live = recipe, puts = 0;
  let restoreRemoval = () => {};
  const fetcher = mockApi((_url, init) => {
    if (init?.method === 'PUT') {
      puts += 1;
      if (puts === 1) { live = {...recipe,revision:3}; return new Response('{}',{status:409}); }
      const removal = vi.spyOn(Storage.prototype,'removeItem').mockImplementation(() => {throw new Error('blocked');});
      restoreRemoval = () => removal.mockRestore();
      return {...live,...JSON.parse(init.body as string),revision:4};
    }
    return live;
  });
  const navigate = vi.fn();
  render(Editor,{recipeId:'r1',navigate});
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'My exact recipe'}});
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  await screen.findByRole('heading',{name:'Review recipe changes'});
  const saveVersion = screen.getByRole('button',{name:'Save my version'});
  await waitFor(() => expect(saveVersion).toBeEnabled());
  await fireEvent.click(saveVersion);
  const replace = screen.getByRole('button',{name:'Replace recipe'});
  await waitFor(() => expect(replace).toBeEnabled());
  await fireEvent.click(replace);
  await waitFor(() => expect(fetcher.mock.calls.filter(([,init]) => init?.method === 'PUT')).toHaveLength(2));
  expect(await screen.findByText('Recipe saved, but its local draft could not be removed.')).toBeInTheDocument();
  expect(screen.queryByRole('button',{name:'Save my version'})).not.toBeInTheDocument();
  restoreRemoval();
  await fireEvent.click(screen.getByRole('button',{name:'Retry draft removal'}));
  expect(navigate).toHaveBeenCalledWith('/recipes/r1');
  expect(fetcher.mock.calls.filter(([,init]) => init?.method === 'PUT')).toHaveLength(2);
  expect(localStorage.getItem('notebook:draft:r1')).toBeNull();
});
it('keeps the editor usable when a late comparison finishes after Back', async () => {
  const pending = (Promise as PromiseConstructor & {withResolvers<T>(): {promise: Promise<T>; resolve(value: T): void; reject(reason?: unknown): void}}).withResolvers<unknown>();
  let gets = 0;
  mockApi((_url, init) => {
    if (init?.method === 'PUT') return new Response('{}',{status:409});
    gets += 1;
    return gets === 1 ? recipe : pending.promise;
  });
  render(Editor,{recipeId:'r1',navigate:vi.fn()});
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Keep editing'}});
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  await screen.findByLabelText('Loading the latest recipe…');
  const back = screen.getByRole('button',{name:'Back to editing'});
  await waitFor(() => expect(back).toBeEnabled());
  await fireEvent.click(back);
  expect(await screen.findByRole('button',{name:'Review changes'})).toHaveFocus();
  pending.resolve({...recipe,revision:3,title:'Late result'});
  await waitFor(() => expect(screen.getByLabelText('Recipe title')).toHaveValue('Keep editing'));
  expect(screen.queryByRole('heading',{name:'Review recipe changes'})).not.toBeInTheDocument();
});
it('retains the draft and prohibits replacement when latest loading fails or permission is lost', async () => {
  let gets = 0;
  const fetcher = mockApi((_url, init) => {
    if (init?.method === 'PUT') return new Response('{}',{status:409});
    gets += 1;
    if (gets === 1) return recipe;
    if (gets === 2) return new Response(JSON.stringify({detail:'Gone'}),{status:404});
    return {...recipe,revision:3,can_edit:false};
  });
  render(Editor,{recipeId:'r1',navigate:vi.fn()});
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Retained draft'}});
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('recipe is unavailable');
  expect(screen.getByRole('button',{name:'Save my version'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Discard my draft'})).toBeEnabled();
  await fireEvent.click(screen.getByRole('button',{name:'Retry comparison'}));
  expect(await screen.findByText(/no longer have permission/)).toBeInTheDocument();
  expect(screen.getByRole('button',{name:'Save my version'})).toBeDisabled();
  expect(JSON.parse(localStorage.getItem('notebook:draft:r1')!).draft.title).toBe('Retained draft');
  expect(fetcher.mock.calls.filter(([,init]) => init?.method === 'PUT')).toHaveLength(1);
});
it('keeps review content accessible when local discard fails and permits a deletion retry', async () => {
  let live = recipe;
  const fetcher = mockApi((_url, init) => {
    if (init?.method === 'PUT') { live = {...recipe,revision:3}; return new Response('{}',{status:409}); }
    return live;
  });
  const navigate = vi.fn();
  render(Editor,{recipeId:'r1',navigate});
  await fireEvent.input(await screen.findByLabelText('Recipe title'),{target:{value:'Do not lose'}});
  await fireEvent.click(screen.getByRole('button',{name:'Save changes'}));
  await screen.findByText(/Latest saved recipe: revision 3/);
  await fireEvent.click(screen.getByRole('button',{name:'Discard my draft'}));
  const originalRemove = Storage.prototype.removeItem;
  const removal = vi.spyOn(Storage.prototype,'removeItem').mockImplementation(function (this: Storage, key) {
    if (key === 'notebook:draft:r1') throw new Error('blocked');
    return Reflect.apply(originalRemove, this, [key]);
  });
  await fireEvent.click(screen.getByRole('button',{name:'Discard draft'}));
  expect(await screen.findByText(/Could not discard this draft/)).toBeInTheDocument();
  expect(navigate).not.toHaveBeenCalled();
  expect(localStorage.getItem('notebook:draft:r1')).not.toBeNull();
  removal.mockRestore();
  await fireEvent.click(screen.getByRole('button',{name:'Discard draft'}));
  expect(navigate).toHaveBeenCalledWith('/recipes/r1');
  expect(fetcher.mock.calls.filter(([,init]) => init?.method === 'PUT')).toHaveLength(1);
});
