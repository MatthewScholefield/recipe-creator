<script lang="ts">
  import { onDestroy, onMount, tick, untrack } from 'svelte';
  import { request, message } from './api';
  import { load, save } from './local';
  import { bookmarkIds, browseUrl, mealGroup, MEAL_CLASSIFIERS, uniqueTags } from './browse-query';
  import { appState } from './app-state.svelte';
  import { draftHref, listDrafts, subscribeDrafts, type DraftSummary } from './drafts';
  import Tag from './ui/Tag.svelte';
  import TagPicker from './ui/TagPicker.svelte';
  import Icon from './ui/Icon.svelte';
  import Button from './ui/Button.svelte';
  import Spinner from './ui/Spinner.svelte';
  import RecipeCard from './RecipeCard.svelte';
  import type { RecipeSummary } from './types';

  type TagCatalog = {tags: string[]; classifier_tags?: string[]};
  type ListResult = {items: RecipeSummary[]; has_more: boolean; errors?: string[]};
  type LookupResult = {items: RecipeSummary[]; unavailable_ids: string[]};
  let {query, selectedTags = [], savedOnly = false, onloadingchange}: {query: string; selectedTags?: string[]; savedOnly?: boolean; onloadingchange?: (loading: boolean) => void} = $props();
  let items = $state<RecipeSummary[]>([]), tags = $state<string[]>([]), classifiers = $state<string[]>([...MEAL_CLASSIFIERS]);
  let more = $state(false), busy = $state(false), error = $state(''), warnings = $state<string[]>([]);
  let tagError = $state(''), drafts = $state<DraftSummary[]>([]);
  let savedProgress = $state({done: 0, total: 0}), unavailable = $state<string[]>([]), invalidBookmarks = $state(0);
  let failedBatches = $state<number[]>([]), generation = 0, controller: AbortController | undefined;
  const selected = $derived(uniqueTags(selectedTags));
  const active = $derived(Boolean(query || selected.length));
  const visible = $derived(savedOnly ? items : [...items].sort((a, b) => a.title.localeCompare(b.title)));
  const groups = $derived(active || savedOnly
    ? [{name: savedOnly ? 'Saved recipes' : 'Results', items: visible}]
    : [...classifiers, 'Other'].map(name => ({name, items: visible.filter(recipe => mealGroup(recipe.tags || [], classifiers) === name)})));
  const quickTags = $derived(tags.filter(tag => !selected.some(value => value.localeCompare(tag, undefined, {sensitivity: 'accent'}) === 0)).slice(0, 6));

  function clipQuickTags(node: HTMLDivElement, _tags: string[]) {
    let disposed = false;
    function measure() {
      const edge = node.getBoundingClientRect().right;
      for (const link of node.querySelectorAll('a')) {
        const clipped = link.getBoundingClientRect().right > edge;
        link.inert = clipped;
        link.style.visibility = clipped ? 'hidden' : '';
        if (clipped) link.setAttribute('aria-hidden', 'true');
        else link.removeAttribute('aria-hidden');
      }
    }
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    async function update() { await tick(); if (!disposed) measure(); }
    void update();
    return {update, destroy() { disposed = true; observer.disconnect(); }};
  }

  function link(nextTags = selected) { return browseUrl({q: query, tags: uniqueTags(nextTags), saved: savedOnly}); }
  function navigate(url: string) { history.pushState({}, '', url); window.dispatchEvent(new PopStateEvent('popstate')); }
  function setTags(value: string[]) { navigate(link(value)); }
  function clearFilters() { navigate(browseUrl({q: '', tags: [], saved: savedOnly})); }
  function requestSignal() { controller?.abort(); return controller = new AbortController(); }
  function recipePath(offset: number) {
    const params = new URLSearchParams({q: query, offset: String(offset), limit: '60'});
    for (const tag of selected) params.append('tag', tag);
    return `/recipes?${params}`;
  }
  async function fetchPage(reset = false) {
    const token = ++generation, signal = requestSignal().signal;
    busy = true; error = '';
    if (reset) {items = []; warnings = []; more = false;}
    const offset = reset ? 0 : items.length;
    try {
      const result = await request<ListResult>(recipePath(offset), {signal});
      if (token !== generation) return;
      items = reset ? result.items : [...items, ...result.items];
      more = result.has_more; warnings = result.errors || [];
    } catch (reason) { if (token === generation) error = message(reason); }
    finally { if (token === generation) busy = false; }
  }
  async function loadSaved(retry: number[] | null = null) {
    const token = ++generation, values = bookmarkIds(load<unknown>('bookmarks', [])), signal = requestSignal().signal;
    invalidBookmarks = values.invalid;
    const chunks = Array.from({length: Math.ceil(values.ids.length / 100)}, (_, index) => values.ids.slice(index * 100, (index + 1) * 100));
    const indexes = retry || chunks.map((_, index) => index);
    busy = true; error = ''; savedProgress = {done: 0, total: indexes.length};
    if (!retry) {items = []; unavailable = []; failedBatches = [];}
    if (!chunks.length) {busy = false; return;}
    const nextItems = retry ? [...items] : [];
    const nextUnavailable = retry ? [...unavailable] : [];
    const failures: number[] = [];
    let next = 0;
    const worker = async () => {
      while (next < indexes.length) {
        const index = indexes[next++];
        try {
          const result = await request<LookupResult>('/recipes/lookup', {method: 'POST', signal, body: JSON.stringify({ids: chunks[index], q: query || undefined, tags: selected})});
          if (token !== generation) return;
          nextItems.push(...result.items);
          nextUnavailable.push(...result.unavailable_ids);
        } catch (reason) {
          if (token !== generation) return;
          failures.push(index); error = message(reason);
        } finally { if (token === generation) savedProgress = {...savedProgress, done: savedProgress.done + 1}; }
      }
    };
    await Promise.all(Array.from({length: Math.min(2, indexes.length)}, worker));
    if (token !== generation) return;
    const order = new Map(values.ids.map((id, index) => [id, index]));
    items = nextItems.sort((left, right) => (order.get(left.id) ?? Infinity) - (order.get(right.id) ?? Infinity));
    unavailable = [...new Set(nextUnavailable)]; failedBatches = failures;
    busy = false;
  }
  function removeUnavailable() {
    const missing = new Set(unavailable);
    const stored = load<unknown>('bookmarks', []);
    if (Array.isArray(stored)) save('bookmarks', stored.filter(value => {
      const id = typeof value === 'string' && value.startsWith('recipes:') ? value.slice('recipes:'.length) : value;
      return typeof id !== 'string' || !missing.has(id);
    }));
    unavailable = [];
  }
  $effect(() => {
    query; selectedTags; savedOnly;
    untrack(() => { if (savedOnly) void loadSaved(); else void fetchPage(true); });
    return () => { generation++; controller?.abort(); };
  });
  $effect(() => onloadingchange?.(busy));
  onDestroy(() => onloadingchange?.(false));
  onMount(() => {
    drafts = listDrafts();
    const unsubscribe = subscribeDrafts(() => drafts = listDrafts());
    void request<TagCatalog>('/tags').then(result => {
      tags = [...(result.tags || [])].sort((left, right) => left.localeCompare(right));
      classifiers = result.classifier_tags?.length ? result.classifier_tags : [...MEAL_CLASSIFIERS];
    }).catch(reason => tagError = `Tags could not be loaded. ${message(reason)}`);
    return unsubscribe;
  });
</script>

<div class="page-heading">
  <div><p class="eyebrow">Recipes</p><h1>{savedOnly ? 'Saved recipes' : active ? 'Results' : appState.copy.home_title}</h1>{#if !savedOnly && !active && appState.copy.home_intro}<p>{appState.copy.home_intro}</p>{/if}</div>
</div>

{#if !savedOnly && drafts.length}<section class="drafts" aria-labelledby="drafts-heading"><div><h2 id="drafts-heading">Drafts</h2></div><div class="cards">{#each drafts as draft (draft.id)}<RecipeCard title={draft.title} href={draftHref(draft.id)} isDraft updatedAt={draft.updatedAt} />{/each}</div></section>{/if}

<section class="tag-filters" aria-label="Recipe filters"><div class="quick-tags" use:clipQuickTags={quickTags}>{#each quickTags as tag}<Tag label={tag} href={link([...selected, tag])} />{/each}</div><TagPicker tags={tags} selected={selected} label="Search tags" placeholder="Search tags" compact allowCreate={false} onchange={setTags} />{#if tagError}<p class="notice" role="status">{tagError}</p>{/if}</section>

{#if selected.length || query}<div class="active-filters" aria-label="Active filters">{#if query}<Tag label={`Search: ${query}`} removable href={browseUrl({q: '', tags: selected, saved: savedOnly})} />{/if}{#each selected as tag}<Tag label={tag} removable href={link(selected.filter(value => value !== tag))} />{/each}</div>{/if}

{#if error}<div role="alert" class="notice error">{error} <button onclick={() => savedOnly ? void loadSaved(failedBatches.length ? failedBatches : null) : void fetchPage(items.length === 0)}>Try again</button></div>{/if}
{#each warnings as warning}<p class="notice" role="status">{warning}</p>{/each}
{#if savedOnly && (invalidBookmarks || unavailable.length)}<div class="notice" role="status">{#if invalidBookmarks}{invalidBookmarks} invalid bookmark{invalidBookmarks === 1 ? '' : 's'} could not be loaded. {/if}{#if unavailable.length}{unavailable.length} saved recipe{unavailable.length === 1 ? ' is' : 's are'} unavailable. <button type="button" onclick={removeUnavailable}>Remove unavailable</button>{/if}</div>{/if}
{#if savedOnly && busy}<p role="status"><Spinner size={16} /> Loading saved recipes ({savedProgress.done} of {savedProgress.total})…</p>{/if}

{#each groups as group}{#if group.items.length}<section class="recipe-group"><h2>{group.name}</h2><div class="cards">{#each group.items as recipe (recipe.id)}<RecipeCard title={recipe.title} href={`/recipes/${encodeURIComponent(recipe.id)}`} description={recipe.description} thumbnailPhotoId={recipe.thumbnail_photo_id} />{/each}</div></section>{/if}{/each}
{#if busy && !savedOnly}<p role="status"><Spinner size={16} /> Loading recipes…</p>{:else if !visible.length && !busy && !error}<div class="empty"><h2>{savedOnly ? 'No saved recipes' : active ? 'No matching recipes' : 'No recipes yet'}</h2><p>{savedOnly ? 'Save recipes to this device to find them here.' : active ? 'Try a different search or filter.' : 'Add the first recipe when you are ready.'}</p>{#if active}<button type="button" onclick={clearFilters}>Clear filters</button>{:else if !savedOnly}<Button variant="primary" size="sm" href="/new"><Icon name="plus" size={18} /> Add recipe</Button>{/if}</div>{/if}
{#if savedOnly && failedBatches.length}<p class="notice" role="status">Some saved recipes could not be loaded. <button type="button" onclick={() => void loadSaved(failedBatches)}>Retry failed requests</button></p>{/if}
{#if more && !savedOnly}<button class="load-more" disabled={busy} onclick={() => void fetchPage()}>Load more recipes</button>{/if}

<style>
  .page-heading,.drafts,.tag-filters,.active-filters{display:flex;gap:1rem;align-items:center;justify-content:space-between}.page-heading{align-items:flex-start;text-align:left}.drafts,.tag-filters,.active-filters{margin:1rem 0;align-items:flex-start}.drafts{display:block}.drafts .cards{grid-template-columns:repeat(auto-fill,minmax(min(100%,20rem),1fr))}.quick-tags,.active-filters{display:flex;gap:.4rem;flex-wrap:wrap}.active-filters{justify-content:flex-start;align-items:center}.tag-filters{justify-content:flex-start;flex-wrap:wrap;align-items:center;gap:.5rem}.quick-tags{flex:0 1 auto;min-width:0;overflow:hidden;white-space:nowrap;flex-wrap:nowrap}.quick-tags :global(.tag){flex:none}.tag-filters :global(.tag-picker){flex:0 1 auto;max-width:100%}.recipe-group{margin:1.5rem 0}
</style>
