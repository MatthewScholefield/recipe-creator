import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { flushSync } from 'svelte';
import Admin from './Admin.svelte';
import AuthorPicker from './AuthorPicker.svelte';
import IdentityPrompt from './IdentityPrompt.svelte';
import App from './App.svelte';
import Profile from './Profile.svelte';
import { createDraft, saveDraft, draftHref } from './drafts';
import { appState, DEFAULT_SITE_COPY, refreshSiteCopy, setSiteCopy } from './app-state.svelte';
const user = {id:'u1',display_name:'Cook',state:'active',photo_trusted:false};
const identity = {user,device_id:'d1',admin:true,csrf_token:'csrf'};
const recipe = {id:'r1',revision:2,owner_id:'u1',author_name:'Cook'};
function mockApi(handler: (url: string, init?: RequestInit) => unknown | Promise<unknown>) {
  const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const result = await handler(String(url), init);
    return result instanceof Response ? result : new Response(JSON.stringify(result));
  });
  vi.stubGlobal('fetch', fetcher); return fetcher;
}
beforeEach(() => {appState.identity = null; setSiteCopy({...DEFAULT_SITE_COPY}); history.replaceState({}, '', '/');});
it('keeps identity inputs unique and Enter local to the name prompt', async () => {
  let named = false;
  const fetcher = mockApi((url) => url === '/api/session' ? {...identity,user:named ? user : null} : (named = true, {}));
  const form = document.createElement('form'); document.body.append(form);
  const publish = vi.fn((event: Event) => event.preventDefault()); form.addEventListener('submit',publish);
  const ready = vi.fn(); render(IdentityPrompt,{target:form,props:{onready:ready}}); render(IdentityPrompt,{onready:vi.fn()});
  const inputs = screen.getAllByLabelText('Your display name'); expect(inputs[0].id).not.toBe(inputs[1].id);
  expect(form.querySelectorAll('form')).toHaveLength(0);
  await fireEvent.input(inputs[0],{target:{value:'Cook'}}); await fireEvent.keyDown(inputs[0],{key:'Enter'});
  await waitFor(() => expect(ready).toHaveBeenCalled()); expect(appState.identity?.user?.display_name).toBe('Cook'); expect(publish).not.toHaveBeenCalled();
  expect(fetcher.mock.calls.filter(([url]) => url === '/api/identity')).toHaveLength(1); form.remove();
});
it('denies admin controls without offering a password login', async () => {
  const fetcher = mockApi(() => ({...identity,admin:false})); render(Admin);
  await screen.findByText(/does not have admin access/); expect(screen.queryByLabelText('Admin password')).not.toBeInTheDocument();
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('/admin/'))).toBe(false);
});
it('retains conflicting copy edits and resets only the preview until Save', async () => {
  const fetcher = mockApi((url,init) => url === '/api/session' ? identity : url === '/api/admin/photos' ? {photos:[]} : init?.method === 'PUT' ? new Response(JSON.stringify({detail:'Conflict'}),{status:409}) : {revision:3,copy:{...DEFAULT_SITE_COPY,site_title:'Existing'}});
  render(Admin); await fireEvent.click(await screen.findByRole('button',{name:'Site text'}));
  await fireEvent.input(await screen.findByLabelText('Site title'),{target:{value:'My title'}});
  await fireEvent.click(screen.getByRole('button',{name:'Save site text'})); await screen.findByRole('alert'); expect(screen.getByLabelText('Site title')).toHaveValue('My title');
  const write = fetcher.mock.calls.find(([,init]) => init?.method === 'PUT'); expect(JSON.parse(write![1]!.body as string).expected_revision).toBe(3);
  await fireEvent.click(screen.getByRole('button',{name:'Reset to defaults'})); expect(screen.getByLabelText('Site title')).toHaveValue('Recipes'); expect(fetcher.mock.calls.filter(([,init]) => init?.method === 'PUT')).toHaveLength(1);
});
it('refreshes shared copy after saving and renders copy as text', async () => {
  mockApi((url,init) => url === '/api/session' ? identity : url === '/api/admin/photos' ? {photos:[]} : {revision:init?.method === 'PUT' ? 1 : 0,copy:init?.body ? JSON.parse(init.body as string).copy : DEFAULT_SITE_COPY});
  render(Admin); await fireEvent.click(await screen.findByRole('button',{name:'Site text'}));
  await fireEvent.input(await screen.findByLabelText('Site title'),{target:{value:'<b>Recipes</b>'}}); await fireEvent.click(screen.getByRole('button',{name:'Save site text'}));
  await screen.findByText('Site text saved.'); expect(appState.copy.site_title).toBe('<b>Recipes</b>'); expect(screen.getByText('<b>Recipes</b>').querySelector('b')).toBeNull();
});
it('keeps neutral defaults on public copy failure and permits retry', async () => {
  let fail = true; mockApi(() => fail ? new Response('{}',{status:503}) : {revision:1,copy:{...DEFAULT_SITE_COPY,site_title:'Kitchen'}});
  await refreshSiteCopy(); expect(appState.copy.site_title).toBe('Recipes'); expect(appState.copyError).toContain('could not be loaded');
  fail = false; await refreshSiteCopy(); expect(appState.copy.site_title).toBe('Kitchen'); expect(appState.copyError).toBe('');
});
it('hides author transfer for non-admins and uses revision guarded explicit save', async () => {
  const fetcher = mockApi((url) => url === '/api/session' ? identity : url.startsWith('/api/admin/users?') ? {items:[{...user,id:'u2'},{...user,id:'u3'}],has_more:false} : {...recipe,owner_id:'u2',revision:3});
  const onchanged = vi.fn(); render(AuthorPicker,{recipe,onchanged}); expect(screen.queryByText('Change author')).not.toBeInTheDocument();
  flushSync(() => appState.identity = identity); await fireEvent.click(screen.getByRole('button',{name:'Change author'}));
  await fireEvent.click(await screen.findByRole('button',{name:'Cook u2'})); expect(onchanged).not.toHaveBeenCalled();
  await fireEvent.click(screen.getByRole('button',{name:'Save author'})); await waitFor(() => expect(onchanged).toHaveBeenCalledWith(expect.objectContaining({revision:3})));
  expect(recipe.revision).toBe(2); const write = fetcher.mock.calls.find(([url]) => String(url).endsWith('/owner')); expect(JSON.parse(write![1]!.body as string)).toEqual({owner_id:'u2',expected_revision:2});
  expect(fetcher.mock.calls.some(([url]) => String(url).includes('eligible_owner=true'))).toBe(true);
});
it('keeps author selection on 409 without allowing a blind overwrite', async () => {
  appState.identity = identity;
  mockApi(url => url === '/api/session' ? identity : url.includes('/users?') ? {items:[{...user,id:'u2'}],has_more:false} : new Response(JSON.stringify({detail:'Conflict'}),{status:409}));
  const onchanged = vi.fn(); render(AuthorPicker,{recipe,onchanged}); await fireEvent.click(screen.getByRole('button',{name:'Change author'}));
  await fireEvent.click(await screen.findByRole('button',{name:'Cook u2'})); await fireEvent.click(screen.getByRole('button',{name:'Save author'}));
  expect(await screen.findByRole('alert')).toHaveTextContent('selection has been kept'); expect(screen.getByRole('button',{name:'Cook u2'})).toHaveAttribute('aria-pressed','true'); expect(screen.getByRole('button',{name:'Save author'})).toBeDisabled(); expect(onchanged).not.toHaveBeenCalled();
});
it('ignores stale author search responses and pages eligible results', async () => {
  appState.identity = identity; let finish!: (value: unknown) => void;
  mockApi(url => url.includes('q=old') ? new Promise(resolve => finish = resolve) : {items:[{...user,id:url.includes('start=20') ? 'next' : 'fresh'}],has_more:!url.includes('start=20')});
  render(AuthorPicker,{recipe,onchanged:vi.fn()}); await fireEvent.click(screen.getByRole('button',{name:'Change author'}));
  await fireEvent.input(screen.getByLabelText('Find a profile'),{target:{value:'old'}}); await waitFor(() => expect(finish).toBeTypeOf('function'));
  await fireEvent.input(screen.getByLabelText('Find a profile'),{target:{value:'new'}}); await screen.findByRole('button',{name:'Cook fresh'});
  finish({items:[{...user,id:'stale'}],has_more:false}); await new Promise(resolve => setTimeout(resolve,20)); expect(screen.queryByRole('button',{name:'Cook stale'})).toBeNull();
  await fireEvent.click(screen.getByRole('button',{name:'More profiles'})); await screen.findByRole('button',{name:'Cook next'});
});
it('shows anonymous profile menu and preserves saved search tag parameters', async () => {
  history.replaceState({}, '', '/saved?tag=dinner&tag=vegan'); vi.stubGlobal('scrollTo',vi.fn());
  mockApi(url => url === '/api/session' ? {...identity,user:null,admin:false} : url === '/api/site-settings' ? {revision:0,copy:DEFAULT_SITE_COPY} : url.includes('/tags') ? {tags:[],classifier_tags:[]} : {items:[],unavailable_ids:[],errors:[],has_more:false});
  render(App); await fireEvent.click(screen.getByRole('button',{name:'Profile menu'})); await screen.findByRole('menuitem',{name:'Set name'});
  await fireEvent.input(screen.getByRole('searchbox',{name:'Search recipes'}),{target:{value:'soup'}}); await fireEvent.submit(screen.getByRole('searchbox',{name:'Search recipes'}).closest('form')!);
  expect(location.pathname).toBe('/saved'); expect(new URLSearchParams(location.search).getAll('tag')).toEqual(['dinner','vegan']); expect(new URLSearchParams(location.search).get('q')).toBe('soup');
});
it('remounts the editor when only the draft query changes', async () => {
  const first = createDraft('First'), second = createDraft('Second'); first.draft.title = 'First recipe'; second.draft.title = 'Second recipe'; saveDraft(first); saveDraft(second);
  history.replaceState({}, '', draftHref(first.id));
  mockApi(url => url === '/api/session' ? identity : {revision:0,copy:DEFAULT_SITE_COPY});
  render(App); expect(await screen.findByLabelText('Recipe title')).toHaveValue('First recipe');
  history.pushState({}, '', draftHref(second.id)); await fireEvent(window,new PopStateEvent('popstate'));
  await waitFor(() => expect(screen.getByLabelText('Recipe title')).toHaveValue('Second recipe'));
  history.replaceState({}, '', draftHref(first.id)); await fireEvent(window,new PopStateEvent('popstate'));
  await waitFor(() => expect(screen.getByLabelText('Recipe title')).toHaveValue('First recipe'));
});
it('requires confirmation to revoke this browser and preserves local records', async () => {
  let revoked = false; localStorage.setItem('notebook:bookmarks','["r1"]'); localStorage.setItem('notebook:draft:v2:draft','keep');
  mockApi((url,init) => url === '/api/session' ? revoked ? {...identity,user:null,admin:false,device_id:null} : identity : init?.method === 'DELETE' ? (revoked = true,{}) : url === '/api/devices' ? {devices:[{id:'d1'}]} : {items:[],has_more:false});
  render(Profile,{consumeToken:vi.fn()}); await screen.findByText(/Browser \(this device\)/); await fireEvent.click(screen.getByText('Devices'));
  await fireEvent.click(screen.getByRole('button',{name:'Revoke'})); expect(revoked).toBe(false); await screen.findByRole('dialog',{name:'Revoke device access?'});
  await fireEvent.click(screen.getByRole('button',{name:'Revoke access'})); await waitFor(() => expect(appState.identity?.user).toBeNull()); expect(localStorage.getItem('notebook:bookmarks')).toBe('["r1"]'); expect(localStorage.getItem('notebook:draft:v2:draft')).toBe('keep');
});
