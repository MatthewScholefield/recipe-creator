<script lang="ts">
  import { request, message } from './api';
  import type { AdminUser } from './types';
  import Spinner from './ui/Spinner.svelte';
  import Icon from './ui/Icon.svelte';

  interface Props {
    label: string;
    selectedId?: string | null;
    selectedName?: string | null;
    disabled?: boolean;
    eligibility?: 'owner' | 'merge';
    excludeId?: string | null;
    help?: string;
    optionsLabel?: string;
    showIds?: boolean;
    onselect: (user: AdminUser) => void | Promise<void>;
  }

  let {
    label,
    selectedId = null,
    selectedName = null,
    disabled = false,
    eligibility = 'owner',
    excludeId = null,
    help,
    optionsLabel = 'Profiles',
    showIds = false,
    onselect,
  }: Props = $props();
  let open = $state(false), query = $state(''), users = $state<AdminUser[]>([]);
  let loading = $state(false), searchError = $state(''), start = $state(0), hasMore = $state(false), retry = $state(0);
  let generation = 0;
  const searchId = $props.id(), listId = `${searchId}-options`;

  $effect(() => {
    if (!open) query = selectedName || '';
  });
  $effect(() => {
    const q = query, offset = start; void retry;
    if (!open) return;
    const current = ++generation, controller = new AbortController();
    loading = true; searchError = '';
    const timer = setTimeout(async () => {
      try {
        const filter = eligibility === 'owner' ? 'eligible_owner=true' : 'eligible_merge=true';
        const result = await request<{items: AdminUser[]; has_more: boolean}>(`/admin/users?q=${encodeURIComponent(q)}&start=${offset}&limit=20&${filter}`, {signal: controller.signal});
        if (controller.signal.aborted || current !== generation) return;
        users = result.items; hasMore = result.has_more;
      } catch (error) {
        if (!controller.signal.aborted && current === generation) searchError = message(error);
      } finally {
        if (!controller.signal.aborted && current === generation) loading = false;
      }
    }, 200);
    return () => { clearTimeout(timer); controller.abort(); };
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
    if (disabled || user.id === excludeId) return;
    query = user.display_name;
    open = false;
    await onselect(user);
  }
</script>

<section class="user-picker" onfocusout={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) open = false; }}>
  <label for={searchId}>{label}</label>
  <div class="combobox">
    <input id={searchId} type="search" role="combobox" aria-expanded={open} aria-controls={listId} aria-autocomplete="list"
      autocomplete="off" maxlength="100" bind:value={query} {disabled}
      onfocus={(event) => { open = true; event.currentTarget.select(); }}
      onkeydown={(event) => { if (event.key === 'Escape') { open = false; event.currentTarget.blur(); } }}
      oninput={(event) => { event.stopPropagation(); open = true; start = 0; }}>
    {#if disabled}<Spinner label="Saving selection" size={16} />{/if}
  </div>
  {#if help}<p class="help">{help}</p>{/if}
  {#if open}
    <div class="options" id={listId} role="listbox" aria-label={optionsLabel}>
      {#if loading}<Spinner label="Finding profiles" />
      {:else if searchError}<p role="alert">{searchError}</p><button type="button" onclick={() => retry++}>Retry search</button>
      {:else}
        {#each users as user (user.id)}
          <button type="button" role="option" aria-selected={selectedId === user.id} disabled={disabled || user.id === excludeId} onclick={() => void choose(user)}>
            <span><strong>{user.display_name}</strong><small>{activity(user.last_login_at)}{eligibility === 'merge' ? ` · ${user.state}` : ''}{showIds ? ` · ${user.id}` : ''}</small></span>
            {#if selectedId === user.id}<Icon name="check" size={16} />{/if}
          </button>
        {/each}
        {#if !users.length}<p>No matching profiles.</p>{/if}
        <div class="toolbar">{#if start > 0}<button type="button" {disabled} onclick={() => start = Math.max(0, start - 20)}>Previous profiles</button>{/if}{#if hasMore}<button type="button" {disabled} onclick={() => start += 20}>More profiles</button>{/if}</div>
      {/if}
    </div>
  {/if}
</section>

<style>
  .user-picker{position:relative}.combobox{display:flex;align-items:center;gap:.5rem}.combobox input{width:100%}.options{position:absolute;z-index:5;width:100%;max-height:18rem;overflow:auto;padding:.35rem;border:1px solid var(--ui-control-border);border-radius:.5rem;background:var(--ui-surface);box-shadow:0 .5rem 1.5rem rgb(0 0 0 / .12)}.options>button{width:100%;display:flex;align-items:center;justify-content:space-between;gap:.75rem;text-align:left}.options span,.options small{display:block}.options small{color:var(--ui-text-muted,#666)}
</style>
