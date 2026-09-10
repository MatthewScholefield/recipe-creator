<script lang="ts">
  import { appState } from './app-state.svelte';
  import { ApiError, request, mutate, message, id } from './api';
  import type { AdminUser, Recipe, OwnerResult } from './types';
  import Spinner from './ui/Spinner.svelte';
  import Icon from './ui/Icon.svelte';

  let {recipe, onchanged}: {recipe: Pick<Recipe, 'id' | 'revision' | 'owner_id' | 'author_name'>; onchanged: (result: OwnerResult) => void} = $props();
  let open = $state(false), query = $state(''), users = $state<AdminUser[]>([]), selectedId = $state<string | null>(null);
  let loading = $state(false), saving = $state(false), error = $state(''), searchError = $state('');
  let start = $state(0), hasMore = $state(false), retry = $state(0);
  let generation = 0;
  const searchId = $props.id(), listId = `${searchId}-options`;

  $effect(() => {
    if (!open && !saving) {
      query = recipe.author_name || '';
      selectedId = recipe.owner_id;
    }
  });
  $effect(() => {
    const active = open && appState.identity?.admin;
    const q = query, offset = start; void retry;
    if (!active) return;
    const current = ++generation, controller = new AbortController();
    loading = true; searchError = '';
    const timer = setTimeout(async () => {
      try {
        const result = await request<{items: AdminUser[]; has_more: boolean}>(`/admin/users?q=${encodeURIComponent(q)}&start=${offset}&limit=20&eligible_owner=true`, {signal: controller.signal});
        if (controller.signal.aborted || current !== generation) return;
        users = result.items; hasMore = result.has_more;
      } catch(e) {
        if (!controller.signal.aborted && current === generation) searchError = message(e);
      } finally {
        if (!controller.signal.aborted && current === generation) loading = false;
      }
    }, 200);
    return () => {clearTimeout(timer); controller.abort();};
  });

  function activity(value: string | null | undefined): string {
    if (!value) return 'Activity unknown';
    const elapsed = Math.max(0, Date.now() - Date.parse(value));
    const minute = 60_000, hour = 60 * minute, day = 24 * hour;
    if (elapsed < minute) return 'Active just now';
    if (elapsed < hour) { const count = Math.floor(elapsed / minute); return `Active ${count} minute${count === 1 ? '' : 's'} ago`; }
    if (elapsed < day) { const count = Math.floor(elapsed / hour); return `Active ${count} hour${count === 1 ? '' : 's'} ago`; }
    const count = Math.floor(elapsed / day);
    return `Active ${count} day${count === 1 ? '' : 's'} ago`;
  }

  async function choose(user: AdminUser) {
    if (saving || !appState.identity?.admin) return;
    if (user.id === recipe.owner_id) {
      selectedId = user.id; query = user.display_name; open = false; error = ''; return;
    }
    const snapshot = {id: recipe.id, revision: recipe.revision};
    selectedId = user.id; query = user.display_name; saving = true; error = '';
    try {
      const result = await mutate<OwnerResult>(`/admin/recipes/${id(snapshot.id)}/owner`, {owner_id: user.id, expected_revision: snapshot.revision});
      if (recipe.id !== snapshot.id || recipe.revision !== snapshot.revision) {
        error = 'This recipe changed. Reload the recipe before changing its author.';
        return;
      }
      onchanged(result); open = false;
    } catch(e) {
      selectedId = recipe.owner_id; query = recipe.author_name || '';
      error = e instanceof ApiError && e.status === 409 ? 'This recipe changed. Reload the recipe before changing its author.' : message(e);
    } finally {
      saving = false;
    }
  }
</script>

{#if appState.identity?.admin}
  <section class="author-field" aria-label="Recipe author" onfocusout={(event) => {if (!event.currentTarget.contains(event.relatedTarget as Node)) open = false;}}>
    <label for={searchId}>Author</label>
    <div class="combobox">
      <input id={searchId} type="search" role="combobox" aria-expanded={open} aria-controls={listId} aria-autocomplete="list"
        autocomplete="off" maxlength="100" bind:value={query} disabled={saving}
        onfocus={(event) => {open = true; event.currentTarget.select();}}
        onkeydown={(event) => {if (event.key === 'Escape') {open = false; event.currentTarget.blur();}}}
        oninput={(event) => {event.stopPropagation(); open = true; start = 0; error = '';}}>
      {#if saving}<Spinner label="Saving author" size={16} />{/if}
    </div>
    <p class="help">Search for a profile to transfer recipe ownership.</p>
    {#if open}
      <div class="options" id={listId} role="listbox" aria-label="Eligible authors">
        {#if loading}<Spinner label="Finding profiles" />
        {:else if searchError}<p role="alert">{searchError}</p><button type="button" onclick={() => retry++}>Retry search</button>
        {:else}
          {#each users as user (user.id)}
            <button type="button" role="option" aria-selected={selectedId === user.id} disabled={saving} onclick={() => void choose(user)}>
              <span><strong>{user.display_name}</strong><small>{activity(user.last_login_at)}</small></span>
              {#if selectedId === user.id}<Icon name="check" size={16} />{/if}
            </button>
          {/each}
          {#if !users.length}<p>No matching profiles.</p>{/if}
          <div class="toolbar">{#if start > 0}<button type="button" disabled={saving} onclick={() => start = Math.max(0, start - 20)}>Previous profiles</button>{/if}{#if hasMore}<button type="button" disabled={saving} onclick={() => start += 20}>More profiles</button>{/if}</div>
        {/if}
      </div>
    {/if}
    {#if error}<p role="alert">{error}</p>{/if}
  </section>
{/if}

<style>
  .author-field{position:relative;margin:1.5rem 0;padding-top:1rem;border-top:1px solid var(--border)}.combobox{display:flex;align-items:center;gap:.5rem}.combobox input{width:100%}.options{position:absolute;z-index:5;width:100%;max-height:18rem;overflow:auto;padding:.35rem;border:1px solid var(--ui-control-border);border-radius:.5rem;background:var(--ui-surface);box-shadow:0 .5rem 1.5rem rgb(0 0 0 / .12)}.options>button{width:100%;display:flex;align-items:center;justify-content:space-between;gap:.75rem;text-align:left}.options span,.options small{display:block}.options small{color:var(--ui-text-muted,#666)}
</style>
