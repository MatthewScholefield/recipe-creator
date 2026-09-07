<script lang="ts">
  import { untrack } from 'svelte';
  import { request, message, photoUrl } from './api';
  import { categories, load } from './local';
  import type { RecipeSummary } from './types';
  let { query }: {query: string} = $props();
  let items = $state<RecipeSummary[]>([]), tags = $state<string[]>([]), more = $state(false), busy = $state(false), error = $state('');
  let warnings = $state<string[]>([]), bookmarksOnly = $state(false);
  let bookmarks = $state(load<string[]>('bookmarks', []));
  let generation = 0;
  async function fetchPage(reset = false) {
    const token = ++generation; busy = true; error = '';
    if (reset) items = [];
    try { const result = await request<{items: RecipeSummary[]; has_more: boolean; errors: string[]}>(`/recipes?q=${encodeURIComponent(query)}&offset=${items.length}&limit=60`); if (token !== generation) return; items = reset ? result.items : [...items, ...result.items]; more = result.has_more; warnings = result.errors || []; }
    catch (e) { if (token === generation) error = message(e); }
    finally { if (token === generation) busy = false; }
  }
  $effect(() => { query; untrack(() => void fetchPage(true)); return () => { generation++; }; });
  $effect(() => { void request<{tags: string[]}>('/tags').then(result => tags = result.tags).catch(() => {}); });
  const visible = $derived(items.filter(item => !bookmarksOnly || bookmarks.includes(item.id)).sort((a,b) => a.title.localeCompare(b.title)));
  const groups = $derived(query ? [{name: 'Search results', items: visible}] : [...categories.map(name => ({name, items: visible.filter(item => item.tags.some(tag => tag.toLowerCase() === name))})), {name: 'Other', items: visible.filter(item => !item.tags.some(tag => categories.includes(tag.toLowerCase())))}]);
</script>
<div class="page-heading"><div><p class="eyebrow">The collection</p><h1>{query ? `Recipes for “${query}”` : 'Something good to make'}</h1><p>Everyday favorites, handwritten discoveries, and recipes worth keeping.</p></div><button aria-pressed={bookmarksOnly} onclick={() => {bookmarks = load('bookmarks', []); bookmarksOnly = !bookmarksOnly;}}>Saved on this device</button></div>
{#if tags.length}<nav class="chips" aria-label="Filter by tag">{#if query}<a href="/">All recipes</a>{/if}{#each tags as tag}<a href={`/?q=${encodeURIComponent(`tag:${tag}`)}`}>{tag}</a>{/each}</nav>{/if}
{#if error}<div role="alert" class="notice error">{error} <button onclick={() => fetchPage(items.length === 0)}>Try again</button></div>{/if}
{#each warnings as warning}<p class="notice" role="status">{warning}</p>{/each}
{#each groups as group}{#if group.items.length}<section class="recipe-group"><h2>{group.name}</h2><div class="cards">{#each group.items as recipe (recipe.id)}<article class="card">{#if recipe.thumbnail_photo_id}<img src={photoUrl(recipe.thumbnail_photo_id, true)} alt="" width="96" height="96" loading="lazy">{/if}<h3><a href={`/recipes/${encodeURIComponent(recipe.id)}`}>{recipe.title}</a></h3><p class="excerpt">{recipe.description}</p><p class="byline">From {recipe.author_name || 'Unknown author'}</p><div class="chips">{#each recipe.tags as tag}<a href={`/?q=${encodeURIComponent(`tag:${tag}`)}`}>{tag}</a>{/each}</div></article>{/each}</div></section>{/if}{/each}
{#if busy}<p role="status">Finding recipes…</p>{:else if !visible.length && !error}<div class="empty"><h2>{bookmarksOnly ? 'No saved recipes in this page of the collection' : 'A little room in the notebook'}</h2><p>{bookmarksOnly ? 'Open a recipe and choose Save for later. Load more to find older bookmarks.' : 'Try a different search, or share the first recipe.'}</p><a href="/new">Add a recipe</a></div>{/if}
{#if more}<button class="load-more" disabled={busy} onclick={() => fetchPage()}>Load more recipes</button>{/if}
