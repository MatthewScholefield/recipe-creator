<script lang="ts">
  import { onMount, tick } from 'svelte';
  import Browse from './Browse.svelte';
  import Detail from './Detail.svelte';
  import IdentityPrompt from './IdentityPrompt.svelte';
  import { translateLegacy } from './local';
  import { browseUrl } from './browse-query';
  import { appState, refreshIdentity, refreshSiteCopy } from './app-state.svelte';
  import { message } from './api';
  import Dropdown from './ui/Dropdown.svelte';
  import Modal from './ui/Modal.svelte';
  import Button from './ui/Button.svelte';
  import BackLink from './ui/BackLink.svelte';
  import Icon from './ui/Icon.svelte';
  import IconButton from './ui/IconButton.svelte';
  import Spinner from './ui/Spinner.svelte';
  let route = $state(location.pathname + location.search);
  let pairingVersion = $state(0);
  let pairingToken = $state(''), naming = $state(false), identityError = $state('');
  let search = $state(new URLSearchParams(location.search).get('q') || ''), resultsLoading = $state(false), mobileSearchOpen = $state(false);
  let searchInput: HTMLInputElement, mobileSearchTrigger: HTMLButtonElement;
  function readRoute() { route = location.pathname + location.search; search = new URLSearchParams(location.search).get('q') || ''; }
  function navigate(path: string, options?: {replace?: boolean}) { if (options?.replace) history.replaceState({}, '', path); else history.pushState({}, '', path); readRoute(); window.scrollTo({top: 0}); }
  async function loadIdentity() {identityError = ''; try {await refreshIdentity();} catch(error) {identityError = message(error);} }
  onMount(() => {
    if (location.hash.startsWith('#pair=')) { pairingToken = location.hash.slice(6); history.replaceState({}, '', '/profile'); }
    else { const legacy = translateLegacy(location.hash); if (legacy) history.replaceState({}, '', legacy); }
    readRoute(); void loadIdentity(); void refreshSiteCopy();
    const click = (event: MouseEvent) => { const link = (event.target as Element).closest('a'); if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target || link.download || link.origin !== location.origin || link.hash) return; event.preventDefault(); navigate(link.pathname + link.search); };
    const hash = () => { if (location.hash.startsWith('#pair=')) {pairingToken = location.hash.slice(6); pairingVersion++; history.replaceState({}, '', '/profile'); readRoute();} else {const next = translateLegacy(location.hash); if (next) { history.replaceState({}, '', next); readRoute(); }} };
    document.addEventListener('click', click); window.addEventListener('popstate', readRoute); window.addEventListener('hashchange', hash);
    return () => { document.removeEventListener('click', click); window.removeEventListener('popstate', readRoute); window.removeEventListener('hashchange', hash); };
  });
  const pathname = $derived(route.split('?')[0]);
  const params = $derived(new URLSearchParams(route.split('?')[1]));
  const recipeMatch = $derived(/^\/recipes\/([^/]+)(\/edit)?$/.exec(pathname));
  const recipeId = $derived(recipeMatch ? decodeURIComponent(recipeMatch[1]) : undefined);
  const draftId = $derived(params.get('draft') ?? undefined);
  const user = $derived(appState.identity?.user);
  const routeSearch = $derived(params.get('q') || '');
  const searchPending = $derived(search !== routeSearch || resultsLoading);
  function submitSearch() {
    resultsLoading = true;
    navigate(browseUrl({q: search, tags: params.getAll('tag'), saved: pathname === '/saved'}));
  }
  function setResultsLoading(loading: boolean) { resultsLoading = loading; }
  async function openMobileSearch() {
    mobileSearchOpen = true;
    await tick();
    searchInput.focus();
  }
  async function closeMobileSearch() {
    mobileSearchOpen = false;
    await tick();
    mobileSearchTrigger.focus();
  }
  $effect(() => {
    search; routeSearch;
    if (search === routeSearch) return;
    const timer = window.setTimeout(submitSearch, 350);
    return () => window.clearTimeout(timer);
  });
</script>
<a class="skip" href="#main">Skip to recipes</a>
<header class="site-header">
  <a class="brand" href="/">{appState.copy.site_title}{#if appState.copy.site_tagline}<span>{appState.copy.site_tagline}</span>{/if}</a>
  <form class="search" class:mobile-open={mobileSearchOpen} role="search" onsubmit={(event) => {event.preventDefault(); submitSearch();}}>
    <span class="search-indicator">{#if searchPending}<Spinner label="Searching recipes" size={18} />{:else}<Icon name="search" size={18} />{/if}</span>
    <label class="sr-only" for="search">Search recipes</label><input bind:this={searchInput} id="search" type="search" bind:value={search} placeholder="Search recipes" autocomplete="off" onkeydown={(event) => {if (event.key === 'Escape' && mobileSearchOpen) {event.preventDefault(); void closeMobileSearch();}}}>
    <button class="search-close" type="button" aria-label="Close recipe search" title="Close recipe search" onclick={() => void closeMobileSearch()}><Icon name="x" size={20} /></button>
  </form>
  <nav aria-label="Main"><Button variant="primary" size="sm" href="/new" ariaLabel="Add recipe" title="Add recipe" class="mobile-add"><Icon name="plus" size={18} /><span class="add-label">Add recipe</span></Button><button bind:this={mobileSearchTrigger} class="mobile-search-toggle" type="button" aria-label="Open recipe search" title="Search recipes" aria-controls="search" aria-expanded={mobileSearchOpen} onclick={() => void openMobileSearch()}><Icon name="search" size={20} /></button><IconButton href="/saved" ariaLabel="Saved recipes" title="Saved recipes"><Icon name="bookmark" size={18} /></IconButton></nav>
  <div class="profile-menu">
    <Dropdown label="Profile menu">
      {#snippet trigger(open)}<button type="button" class:anonymous={!user} aria-label="Profile menu" aria-haspopup="menu" aria-expanded={open}><Icon name="user" /><span>{user?.display_name || 'Anonymous'}</span><Icon name="chevron-down" size={16} /></button>{/snippet}
      {#if user}<a role="menuitem" href="/profile">My profile</a>{#if appState.identity?.admin}<a role="menuitem" href="/admin">Admin</a>{/if}
      {:else}<button role="menuitem" type="button" onclick={() => naming = true}>Set name</button><a role="menuitem" href="/profile">Connect an existing profile</a>{/if}
    </Dropdown>
  </div>
</header>
<Modal bind:open={naming} title="Set your name"><IdentityPrompt onready={() => naming = false} /></Modal>
{#if appState.copyError}<p class="notice" role="status">{appState.copyError} <button type="button" onclick={() => void refreshSiteCopy()}>Retry site text</button></p>{/if}
{#if identityError}<p class="notice" role="status">Profile could not be loaded. {identityError} <button type="button" onclick={() => void loadIdentity()}>Retry profile</button></p>{/if}
<main id="main" tabindex="-1">
  {#key pathname}
    {#if pathname === '/' || pathname === '/saved'}<Browse query={routeSearch} selectedTags={params.getAll('tag')} savedOnly={pathname === '/saved'} onloadingchange={setResultsLoading} />
    {:else if pathname === '/new' || recipeMatch?.[2]}
      {#key `${recipeId || ''}:${draftId || ''}`}{#await import('./Editor.svelte')}<Spinner label="Loading editor" />{:then {default: Editor}}<Editor {recipeId} {draftId} {navigate} />{:catch}<p role="alert">Could not load the editor. Reload to try again.</p>{/await}{/key}
    {:else if recipeId}<Detail {recipeId} {navigate} />
    {:else if pathname === '/profile'}
      {#key pairingVersion}{#await import('./Profile.svelte')}<Spinner label="Loading profile" />{:then {default: Profile}}<Profile token={pairingToken} consumeToken={() => pairingToken = ''} />{:catch}<p role="alert">Could not load your profile. Reload to try again.</p>{/await}{/key}
    {:else if pathname === '/admin'}
      {#await import('./Admin.svelte')}<Spinner label="Loading admin" />{:then {default: Admin}}<Admin />{:catch}<p role="alert">Could not load admin. Reload to try again.</p>{/await}
    {:else}<h1>Page not found</h1><BackLink href="/" label="Back to recipes" />{/if}
  {/key}
</main>
{#if appState.copy.footer_text}<footer>{appState.copy.footer_text}</footer>{/if}
<style>
  .search{position:relative;align-items:center}.search-indicator{position:absolute;left:.85rem;z-index:1;display:inline-flex;color:var(--muted);pointer-events:none}.search input{width:100%;padding-left:2.55rem;border-radius:999px;background:var(--paper);box-shadow:0 1px 2px rgb(32 63 44/.06)}.search input:focus{border-color:var(--green);box-shadow:0 0 0 3px rgb(53 91 67/.14)}.mobile-search-toggle,.search-close{display:none}
  .profile-menu{margin-left:auto}.profile-menu :global(button){display:flex;align-items:center;gap:.4rem}.anonymous{color:var(--ui-text-muted,#666)}
  @media(max-width:620px){
    .site-header{position:relative;flex-wrap:nowrap;gap:.35rem;min-height:76px}
    .brand{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}.brand span{overflow:hidden;text-overflow:ellipsis}
    .site-header nav{margin-left:0;gap:.25rem}
    .profile-menu{margin-left:0}.profile-menu :global(button){width:44px;padding:0;gap:0}.profile-menu :global(button > span),.profile-menu :global(button > svg:last-child){display:none}
    .add-label{display:none}.site-header :global(.mobile-add){width:44px;padding:0;border-radius:50%}
    .mobile-search-toggle,.search-close{display:inline-flex;align-items:center;justify-content:center;width:44px;height:44px;flex:none;padding:0;border-radius:50%;background:var(--paper)}
    .search{display:none}.search.mobile-open{position:absolute;inset:0;z-index:4;display:flex;order:initial;flex-basis:auto;gap:.5rem;margin:0;padding:1rem;background:#f7f4ec}.search.mobile-open .search-indicator{left:1.85rem}.search.mobile-open input{min-width:0;height:44px}
  }
</style>
