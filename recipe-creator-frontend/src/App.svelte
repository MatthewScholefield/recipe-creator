<script lang="ts">
  import { onMount } from 'svelte';
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
  let search = $state(new URLSearchParams(location.search).get('q') || '');
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
</script>
<a class="skip" href="#main">Skip to recipes</a>
<header class="site-header">
  <a class="brand" href="/">{appState.copy.site_title}{#if appState.copy.site_tagline}<span>{appState.copy.site_tagline}</span>{/if}</a>
  <form class="search" onsubmit={(event) => {event.preventDefault(); navigate(browseUrl({q: search, tags: params.getAll('tag'), saved: pathname === '/saved'}));}}>
    <label class="sr-only" for="search">Search recipes</label><input id="search" type="search" bind:value={search} placeholder="Search recipes"><button aria-label="Search recipes"><Icon name="search" /></button>
  </form>
  <nav aria-label="Main"><Button variant="primary" size="sm" href="/new"><Icon name="plus" size={18} /> Add recipe</Button><IconButton href="/saved" ariaLabel="Saved recipes" title="Saved recipes"><Icon name="bookmark" size={18} /></IconButton></nav>
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
    {#if pathname === '/' || pathname === '/saved'}<Browse query={params.get('q') || ''} selectedTags={params.getAll('tag')} savedOnly={pathname === '/saved'} />
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
  .profile-menu{margin-left:auto}.profile-menu :global(button){display:flex;align-items:center;gap:.4rem}.anonymous{color:var(--ui-text-muted,#666)}
  @media(max-width:600px){.profile-menu :global(button span){max-width:8rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
</style>
