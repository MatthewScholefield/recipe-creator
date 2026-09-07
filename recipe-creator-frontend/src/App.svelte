<script lang="ts">
  import { onMount } from 'svelte';
  import Browse from './Browse.svelte';
  import Detail from './Detail.svelte';
  import { translateLegacy } from './local';
  let route = $state(location.pathname + location.search);
  let pairingToken = $state('');
  let search = $state(new URLSearchParams(location.search).get('q') || '');
  function readRoute() { route = location.pathname + location.search; search = new URLSearchParams(location.search).get('q') || ''; }
  function navigate(path: string) { history.pushState({}, '', path); readRoute(); window.scrollTo({top: 0}); }
  onMount(() => {
    if (location.hash.startsWith('#pair=')) { pairingToken = location.hash.slice(6); history.replaceState({}, '', '/profile'); }
    else { const legacy = translateLegacy(location.hash); if (legacy) history.replaceState({}, '', legacy); }
    readRoute();
    const click = (event: MouseEvent) => { const link = (event.target as Element).closest('a'); if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey || link.target || link.download || link.origin !== location.origin || link.hash) return; event.preventDefault(); navigate(link.pathname + link.search); };
    const hash = () => { const next = translateLegacy(location.hash); if (next) { history.replaceState({}, '', next); readRoute(); } };
    document.addEventListener('click', click); window.addEventListener('popstate', readRoute); window.addEventListener('hashchange', hash);
    return () => { document.removeEventListener('click', click); window.removeEventListener('popstate', readRoute); window.removeEventListener('hashchange', hash); };
  });
  const pathname = $derived(route.split('?')[0]);
  const recipeMatch = $derived(/^\/recipes\/([^/]+)(\/edit)?$/.exec(pathname));
</script>
<a class="skip" href="#main">Skip to recipes</a>
<header class="site-header"><a class="brand" href="/">Our recipe notebook<span>Good food, passed around.</span></a><form class="search" onsubmit={(event) => {event.preventDefault(); navigate(`/?q=${encodeURIComponent(search)}`);}}><label class="sr-only" for="search">Search recipes or tag:dessert</label><input id="search" type="search" bind:value={search} placeholder="Find a recipe or tag:dessert"><button aria-label="Search recipes">Search</button></form><nav aria-label="Main"><a class="button primary" href="/new">Add recipe</a><a href="/profile">Profile</a></nav></header>
<main id="main" tabindex="-1">
  {#key pathname}
    {#if pathname === '/'}<Browse query={new URLSearchParams(route.split('?')[1]).get('q') || ''} />
    {:else if pathname === '/new' || recipeMatch?.[2]}
      {#await import('./Editor.svelte')}<p role="status">Opening your notebook…</p>{:then {default: Editor}}<Editor recipeId={recipeMatch ? decodeURIComponent(recipeMatch[1]) : undefined} {navigate} />{:catch}<p role="alert">Could not load the editor. Reload to try again.</p>{/await}
    {:else if recipeMatch}<Detail recipeId={decodeURIComponent(recipeMatch[1])} {navigate} />
    {:else if pathname === '/profile'}
      {#await import('./Profile.svelte')}<p role="status">Loading profile…</p>{:then {default: Profile}}<Profile token={pairingToken} consumeToken={() => pairingToken = ''} />{:catch}<p role="alert">Could not load your profile. Reload to try again.</p>{/await}
    {:else if pathname === '/admin'}
      {#await import('./Admin.svelte')}<p role="status">Loading admin…</p>{:then {default: Admin}}<Admin />{:catch}<p role="alert">Could not load admin. Reload to try again.</p>{/await}
    {:else}<h1>That page is not in the notebook</h1><a href="/">Back to recipes</a>{/if}
  {/key}
</main>
<footer>Made to be cooked from. Shared by friends and family.</footer>
