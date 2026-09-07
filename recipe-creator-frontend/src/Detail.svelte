<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { request, mutate, message, id } from './api';
  import { load, save, safeUrl } from './local';
  import { ingredientText, quantity } from './recipe';
  import type { Recipe } from './types';
  let { recipeId, navigate }: { recipeId: string; navigate: (path: string) => void } = $props();
  let recipe = $state<Recipe>(), error = $state(''), status = $state(''), scale = $state(1), grams = $state(load('grams', false)), originals = $state(false);
  let checked = $state<string[]>(untrack(() => load(`checked:${recipeId}`, []))), bookmarks = $state<string[]>(load('bookmarks', []));
  let photosOpen = $state(false), awake = $state(false), deleting = $state(false);
  let lock: WakeLockSentinel | undefined;
  let alive = true;
  const lifetime = new AbortController();
  async function fetchRecipe() { error = ''; try { const result = await request<Recipe>(`/recipes/${id(recipeId)}`, {signal: lifetime.signal}); if (alive) recipe = result; } catch(e) { if (alive) error = message(e); } }
  onMount(() => { void fetchRecipe(); return () => { alive = false; lifetime.abort(); void lock?.release(); }; });
  $effect(() => { save('grams', grams); save(`checked:${recipeId}`, checked); save('bookmarks', bookmarks); });
  const factor = $derived(Number.isFinite(scale) && scale > 0 ? scale : 1);
  async function wake() { try { if (awake) { await lock?.release(); awake = false; } else if ('wakeLock' in navigator) { lock = await navigator.wakeLock.request('screen'); if (!alive) {await lock.release(); return;} awake = true; lock.addEventListener('release', () => awake = false); } else status = 'Keeping the screen awake is not supported by this browser.'; } catch { status = 'Could not keep the screen awake. Check your power-saving settings.'; } }
  async function share() { try { if (navigator.share) await navigator.share({title: recipe?.title, url: location.href}); else { await navigator.clipboard.writeText(location.href); status = 'Recipe link copied.'; } } catch(e) { if (!(e instanceof DOMException && e.name === 'AbortError')) status = `Copy this link: ${location.href}`; } }
  async function removeRecipe() { if (!recipe || !confirm('Delete this recipe? An administrator can restore it.')) return; deleting = true; try { await mutate(`/recipes/${id(recipeId)}?expected_revision=${recipe.revision}`, undefined, 'DELETE', lifetime.signal); if (alive) navigate('/'); } catch(e) { if (alive) error = message(e); } finally { deleting = false; } }
</script>
{#if error}<p class="notice error" role="alert">{error} <button onclick={fetchRecipe}>Reload recipe</button>
</p>{/if}
{#if recipe}
<article class="recipe">
<a class="back" href="/">← The collection</a>
<p class="eyebrow">From {recipe.author_name || 'Unknown author'}</p>
<h1>{recipe.title}</h1>
<p class="prose lead">{recipe.description}</p>
<div class="chips">{#each recipe.tags as tag}<a href={`/?q=${encodeURIComponent(`tag:${tag}`)}`}>{tag}</a>{/each}</div>
{#if recipe.yield_amount}<p>Makes {recipe.yield_amount} {recipe.yield_unit}</p>{/if}
{#if safeUrl(recipe.source_url)}<p>
<a href={safeUrl(recipe.source_url)} target="_blank" rel="noopener noreferrer">Original source ↗</a>
</p>{/if}
{#if recipe.modifications}<section>
<h2>Our modifications</h2>
<p class="prose">{recipe.modifications}</p>
</section>{/if}
<div class="toolbar no-print">
<button aria-pressed={bookmarks.includes(recipe.id)} onclick={() => bookmarks = bookmarks.includes(recipe!.id) ? bookmarks.filter(value => value !== recipe!.id) : [...bookmarks, recipe!.id]}>{bookmarks.includes(recipe.id) ? 'Saved ✓' : 'Save for later'}</button>
<button onclick={share}>Share</button>
<button onclick={() => window.print()}>Print</button>
<button aria-pressed={awake} onclick={wake}>{awake ? 'Screen staying awake' : 'Keep screen awake'}</button>{#if recipe.can_edit}<a class="button" href={`/recipes/${id(recipe.id)}/edit`}>Edit recipe</a>
<button class="danger" disabled={deleting} onclick={removeRecipe}>Delete</button>{/if}</div>
{#if status}<p role="status" class="notice">{status}</p>{/if}
{#if recipe.mode === 'text'}<p class="notice">Shared as written. Ingredient scaling and gram tools are unavailable until this recipe is organized.</p>
<div class="prose recipe-body">{recipe.source_text}</div>{#if recipe.can_edit}<a href={`/recipes/${id(recipe.id)}/edit`}>Organize ingredients in the editor</a>{/if}
{:else}
<section class="cooking-controls no-print" aria-label="Cooking controls">
<div class="toolbar">
<span>Scale ingredients</span>{#each [0.5,1,2] as preset}<button aria-pressed={factor === preset} onclick={() => scale = preset}>{preset}×</button>{/each}<label>Custom multiplier<input type="number" min="0.01" max="1000" step="any" bind:value={scale}>
</label>{#if quantity(recipe.yield_amount)}<label>Servings / yield<input type="number" min="0.01" step="any" value={quantity(recipe.yield_amount)! * factor} onchange={(event) => {const value = Number(event.currentTarget.value); if (value > 0) scale = value / quantity(recipe!.yield_amount)!;}}>
</label>{/if}</div>
<p>Only ingredients are scaled. Directions, cooking times, and temperatures stay as written.</p>
<div class="toolbar">
<button aria-pressed={!grams} onclick={() => grams = false}>Original units</button>
<button aria-pressed={grams} onclick={() => grams = true}>Grams</button>
<label class="inline">
<input type="checkbox" bind:checked={originals}>Show originals</label>
<button onclick={() => checked = []}>Reset ingredient checks</button>
</div>{#if grams}<p>≈ means estimated. Unknown weights stay in their original units.</p>{/if}</section>
<div class="recipe-columns">
<section>
<h2>Ingredients</h2>{#each recipe.ingredient_groups as group}<section class="ingredients">{#if group.name}<h3>{group.name}</h3>{/if}{#each group.ingredients as row}<div class:checked={checked.includes(row.id)}>
<label class="ingredient">
<input type="checkbox" checked={checked.includes(row.id)} onchange={() => checked = checked.includes(row.id) ? checked.filter(value => value !== row.id) : [...checked, row.id]}>
<span>{ingredientText(row, factor, grams)}</span>
</label>{#if originals}<p class="original">As written: {row.original_text || ingredientText(row)}</p>{:else}<details class="original">
<summary>Original & weight details</summary>
<p>{row.original_text || ingredientText(row)}</p>{#if row.grams}<p>{row.grams.basis || ''}</p>{/if}</details>{/if}</div>{/each}</section>{/each}</section>
<section>
<h2>Directions</h2>
<div class="prose">{recipe.directions}</div>
</section>
</div>
{#if recipe.notes}<section>
<h2>Notes</h2>
<div class="prose">{recipe.notes}</div>
</section>{/if}{#if recipe.unclassified}<section>
<h2>Other original text</h2>
<div class="prose">{recipe.unclassified}</div>
</section>{/if}
{/if}
{#if recipe.enrichment_status && recipe.enrichment_status !== 'complete'}<p class="no-print">Ingredient weights: {recipe.enrichment_status}{#if recipe.can_edit}<button onclick={async () => {try { await mutate(`/recipes/${id(recipeId)}/enrich`); status = 'Weight estimates queued.'; } catch(e) {error = message(e);}}}>Retry weight estimates</button>{/if}</p>{/if}
<section class="no-print">
<h2>From other kitchens</h2>
<p>A real-life photo is always welcome. Recipes do not need one to be delicious.</p>
<button onclick={() => photosOpen = !photosOpen}>{photosOpen ? 'Close photos' : 'View photos / add yours'}</button>{#if photosOpen}{#await import('./Photos.svelte')}<p role="status">Loading photos…</p>{:then {default: Photos}}<Photos {recipeId} />{:catch}<p role="alert">Could not load photos. Try reloading.</p>{/await}{/if}</section>
</article>
{:else if !error}<p role="status">Opening the recipe…</p>{/if}
