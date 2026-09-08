<script lang="ts">
  import { appState } from './app-state.svelte';
  import { ApiError, request, mutate, message, id } from './api';
  import type { Recipe, OwnerResult, User } from './types';
  import Modal from './ui/Modal.svelte';
  import Spinner from './ui/Spinner.svelte';
  import Icon from './ui/Icon.svelte';
  let {recipe, onchanged}: {recipe: Pick<Recipe, 'id' | 'revision' | 'owner_id' | 'author_name'>; onchanged: (result: OwnerResult) => void} = $props();
  let open = $state(false), query = $state(''), users = $state<User[]>([]), selected = $state<User | null>(null);
  let loading = $state(false), saving = $state(false), error = $state(''), searchError = $state(''), conflict = $state(false);
  let start = $state(0), hasMore = $state(false), retry = $state(0);
  let generation = 0;
  let snapshot = $state({id: '', revision: 0});
  const searchId = $props.id();
  function show() {snapshot = {id: recipe.id, revision: recipe.revision}; query = ''; selected = null; users = []; start = 0; error = ''; conflict = false; open = true;}
  $effect(() => {
    const active = open && appState.identity?.admin;
    const q = query, offset = start; void retry;
    if (!active) return;
    const current = ++generation, controller = new AbortController();
    loading = true; searchError = '';
    const timer = setTimeout(async () => {
      try {
        const result = await request<{items: User[]; has_more: boolean}>(`/admin/users?q=${encodeURIComponent(q)}&start=${offset}&limit=20&eligible_owner=true`, {signal: controller.signal});
        if (controller.signal.aborted || current !== generation) return;
        users = result.items; hasMore = result.has_more;
      } catch(e) {if (!controller.signal.aborted && current === generation) searchError = message(e);}
      finally {if (!controller.signal.aborted && current === generation) loading = false;}
    }, 200);
    return () => {clearTimeout(timer); controller.abort();};
  });
  async function save() {
    if (!selected || saving || conflict || !appState.identity?.admin) return;
    if (recipe.id !== snapshot.id || recipe.revision !== snapshot.revision) {conflict = true; error = 'This recipe changed. Reload the recipe before changing its author.'; return;}
    saving = true; error = '';
    try {
      const result = await mutate<OwnerResult>(`/admin/recipes/${id(snapshot.id)}/owner`, {owner_id: selected.id, expected_revision: snapshot.revision});
      open = false; onchanged(result);
    } catch(e) {conflict = e instanceof ApiError && e.status === 409; error = conflict ? 'This recipe changed. Reload the recipe before changing its author. Your selection has been kept.' : message(e);}
    finally {saving = false;}
  }
</script>
{#if appState.identity?.admin}
  <button type="button" class="author-trigger" disabled={saving} onclick={show}><Icon name="edit" size={16} /> Change author</button>
  <Modal bind:open title="Change author">
    <p>This transfers recipe ownership. The selected person will be able to edit this recipe.</p>
    <p>Current author: {recipe.author_name || 'Unknown'}</p>
    <label for={searchId}>Find a profile</label><input id={searchId} type="search" maxlength="100" bind:value={query} disabled={saving} oninput={() => {start = 0;}}>
    {#if loading}<Spinner label="Finding profiles" />
    {:else if searchError}<p role="alert">{searchError}</p><button type="button" onclick={() => retry++}>Retry search</button>
    {:else}<ul class="authors" aria-label="Eligible authors">{#each users as user (user.id)}<li><button type="button" aria-pressed={selected?.id === user.id} disabled={saving} onclick={() => selected = user}><span>{user.display_name}</span><small>{user.id}</small>{#if selected?.id === user.id}<Icon name="check" size={16} />{/if}</button></li>{/each}</ul>
      {#if !users.length}<p>No matching profiles.</p>{/if}
      <div class="toolbar">{#if start > 0}<button type="button" disabled={saving} onclick={() => start = Math.max(0, start - 20)}>Previous profiles</button>{/if}{#if hasMore}<button type="button" disabled={saving} onclick={() => start += 20}>More profiles</button>{/if}</div>
    {/if}
    {#if selected}<p>Transfer ownership to <strong>{selected.display_name}</strong> ({selected.id})?</p>{/if}
    {#if error}<p role="alert">{error}</p>{/if}
    <div class="toolbar"><button type="button" class="primary" disabled={!selected || selected.id === recipe.owner_id || saving || conflict} onclick={() => void save()}>Save author</button><button type="button" disabled={saving} onclick={() => open = false}>Cancel</button></div>
    {#if saving}<Spinner label="Saving author" />{/if}
  </Modal>
{/if}
<style>
  .author-trigger{display:inline-flex;align-items:center;gap:.3rem}.authors{list-style:none;margin:.75rem 0;padding:0}.authors button{width:100%;display:flex;align-items:center;gap:.5rem;justify-content:space-between;text-align:left}.authors small{overflow-wrap:anywhere;color:var(--ui-text-muted,#666)}
</style>
