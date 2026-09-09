<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { request, mutate, message, id } from './api';
  import { load, save, safeUrl } from './local';
  import { ingredientText, quantity } from './recipe';
  import type { Ingredient, Recipe } from './types';
  import AuthorPicker from './AuthorPicker.svelte';
  import Photos from './Photos.svelte';
  import Button from './ui/Button.svelte';
  import Icon from './ui/Icon.svelte';
  import Tag from './ui/Tag.svelte';
  import Modal from './ui/Modal.svelte';
  import Spinner from './ui/Spinner.svelte';
  import Tooltip from './ui/Tooltip.svelte';
  import BackLink from './ui/BackLink.svelte';

  let { recipeId, navigate }: { recipeId: string; navigate: (path: string) => void } = $props();
  let recipe = $state<Recipe>(), error = $state(''), status = $state(''), scale = $state(1), grams = $state(load('grams', false));
  let checked = $state<string[]>(untrack(() => load(`checked:${recipeId}`, []))), bookmarks = $state<string[]>(load('bookmarks', []));
  let awake = $state(false), deleting = $state(false), deleteOpen = $state(false), controlsOpen = $state(false);
  let lock: WakeLockSentinel | undefined;
  let alive = true;
  const lifetime = new AbortController();

  async function fetchRecipe() { error = ''; try { const result = await request<Recipe>(`/recipes/${id(recipeId)}`, {signal: lifetime.signal}); if (alive) recipe = result; } catch(e) { if (alive) error = message(e); } }
  onMount(() => { void fetchRecipe(); return () => { alive = false; lifetime.abort(); void lock?.release(); }; });
  $effect(() => { save('grams', grams); save(`checked:${recipeId}`, checked); save('bookmarks', bookmarks); });
  const factor = $derived(Number.isFinite(scale) && scale > 0 ? scale : 1);

  async function wake() { try { if (awake) { await lock?.release(); awake = false; } else if ('wakeLock' in navigator) { lock = await navigator.wakeLock.request('screen'); if (!alive) {await lock.release(); return;} awake = true; lock.addEventListener('release', () => awake = false); } else status = 'Keeping the screen awake is not supported by this browser.'; } catch { status = 'Could not keep the screen awake. Check your power-saving settings.'; } }
  async function share() { try { if (navigator.share) await navigator.share({title: recipe?.title, url: location.href}); else { await navigator.clipboard.writeText(location.href); status = 'Recipe link copied.'; } } catch(e) { if (!(e instanceof DOMException && e.name === 'AbortError')) status = `Copy this link: ${location.href}`; } }
  async function removeRecipe() { if (!recipe) return; deleting = true; error = ''; try { await mutate(`/recipes/${id(recipeId)}?expected_revision=${recipe.revision}`, undefined, 'DELETE', lifetime.signal); if (alive) navigate('/'); } catch(e) { if (alive) error = message(e); } finally { if (alive) { deleting = false; deleteOpen = false; } } }
  function updateAuthor(result: Pick<Recipe, 'owner_id' | 'author_name' | 'revision'>) { if (recipe) recipe = {...recipe, ...result}; }
  function scaledAmount(value: number | string) { return new Intl.NumberFormat('en', {maximumFractionDigits: 3}).format(Number(value) * factor); }
  function hasGramAmount(row: Ingredient) { return row.grams?.amount != null && quantity(String(row.grams.amount)) !== null; }
  function gramTail(row: Ingredient) { return `${row.name || row.original_text}${row.preparation ? `, ${row.preparation}` : ''}${row.optional ? ' (optional)' : ''}`; }
</script>
{#if error}<p class="notice error" role="alert">{error} <button onclick={fetchRecipe}>Reload recipe</button></p>{/if}
{#if recipe}
<article class="recipe">
<BackLink href="/" label="Back to collection" />
<div class="byline"><p class="eyebrow">From {recipe.author_name || 'Unknown author'}</p><AuthorPicker {recipe} onchanged={updateAuthor} /></div>
<h1>{recipe.title}</h1>
<p class="prose lead">{recipe.description}</p>
<div class="chips">{#each recipe.tags as tag}<Tag label={tag} href={`/?q=${encodeURIComponent(`tag:${tag}`)}`} />{/each}</div>
{#if recipe.yield_amount}<p>Makes {recipe.yield_amount} {recipe.yield_unit}</p>{/if}
{#if safeUrl(recipe.source_url)}<p><Button variant="ghost" size="sm" href={safeUrl(recipe.source_url)} target="_blank" rel="noopener noreferrer"><Icon name="external-link" size={16} />Original source</Button></p>{/if}
{#if recipe.modifications}<section><h2>Our modifications</h2><p class="prose">{recipe.modifications}</p></section>{/if}
<div class="toolbar no-print">
  <Button variant="ghost" size="sm" ariaLabel={bookmarks.includes(recipe.id) ? 'Remove saved recipe' : 'Save for later'} onclick={() => bookmarks = bookmarks.includes(recipe!.id) ? bookmarks.filter(value => value !== recipe!.id) : [...bookmarks, recipe!.id]}><Icon name="bookmark" color="var(--ui-accent)" fill={bookmarks.includes(recipe.id) ? 'var(--ui-accent)' : undefined} label={bookmarks.includes(recipe.id) ? 'Saved' : 'Save for later'} /></Button>
  <Button variant="ghost" size="sm" onclick={share}><Icon name="share" size={17} />Share</Button>
  <Button variant="ghost" size="sm" onclick={() => window.print()}>Print</Button>
  <button class="small-control" aria-label={awake ? 'Stop keeping screen awake' : 'Keep screen awake'} aria-pressed={awake} onclick={wake}><Icon name="edit" label={awake ? 'Screen staying awake' : 'Keep screen awake'} /></button>
  {#if recipe.can_edit}<Button variant="secondary" size="sm" href={`/recipes/${id(recipe.id)}/edit`}><Icon name="edit" size={17} />Edit</Button><Button variant="ghost" size="sm" ariaLabel="Delete recipe" onclick={() => deleteOpen = true}><Icon name="trash" label="Delete recipe" /></Button>{/if}
</div>
{#if status}<p role="status" class="notice">{status}</p>{/if}
{#if recipe.mode === 'text'}<p class="notice">Shared as written. Ingredient scaling and gram tools are unavailable until this recipe is organized.</p>
<div class="prose recipe-body">{recipe.source_text}</div>{#if recipe.can_edit}<Button variant="secondary" size="sm" href={`/recipes/${id(recipe.id)}/edit`}><Icon name="edit" size={16} />Organize ingredients</Button>{/if}
{:else}
<section class="cooking-controls no-print" aria-label="Cooking controls">
  <div class="controls-heading"><h2>Ingredients</h2><Tooltip text="Adjust servings and units"><button class="small-control" aria-label="Adjust ingredient scale" aria-expanded={controlsOpen} onclick={() => controlsOpen = !controlsOpen}><Icon name="edit" label="Adjust ingredient scale" /></button></Tooltip></div>
  {#if controlsOpen}<div class="scale-panel">
    <div class="toolbar"><span>Scale</span>{#each [0.5,1,2] as preset}<button class="small-control" class:selected={factor === preset} aria-pressed={factor === preset} onclick={() => scale = preset}>{preset}×</button>{/each}<label>Custom multiplier<input type="number" min="0.01" max="1000" step="any" bind:value={scale}></label>{#if quantity(recipe.yield_amount)}<label>Servings<input type="number" min="0.01" step="any" value={quantity(recipe.yield_amount)! * factor} onchange={(event) => {const value = Number(event.currentTarget.value); if (value > 0) scale = value / quantity(recipe!.yield_amount)!;}}></label>{/if}</div>
    <div class="toolbar"><button class="small-control" class:selected={!grams} aria-pressed={!grams} onclick={() => grams = false}>Original units</button><button class="small-control" class:selected={grams} aria-pressed={grams} onclick={() => grams = true}>Grams</button><Button variant="ghost" size="sm" onclick={() => checked = []}>Reset checks</Button></div>
  </div>{/if}
</section>
<div class="recipe-columns">
<section><h2 class="sr-only">Ingredients</h2>{#each recipe.ingredient_groups as group}<section class="ingredients">{#if group.name}<h3>{group.name}</h3>{/if}{#each group.ingredients as row}<div class:checked={checked.includes(row.id)}><label class="ingredient"><input type="checkbox" checked={checked.includes(row.id)} onchange={() => checked = checked.includes(row.id) ? checked.filter(value => value !== row.id) : [...checked, row.id]}>{#if grams && hasGramAmount(row)}<span>{row.grams?.estimated === false ? '' : '≈ '}<Tooltip text={`As written: ${row.original_text || ingredientText(row)}`}><button type="button" class="gram-amount" style="border:0;padding:0;background:none;color:inherit;font:inherit">{scaledAmount(row.grams!.amount!)} g</button></Tooltip>{' '}{gramTail(row)}</span>{:else}<span>{ingredientText(row, factor, grams)}</span>{/if}</label></div>{/each}</section>{/each}</section>
<section><h2>Directions</h2><div class="prose">{recipe.directions}</div></section>
</div>
{#if recipe.notes}<section><h2>Notes</h2><div class="prose">{recipe.notes}</div></section>{/if}{#if recipe.unclassified}<section><h2>Other original text</h2><div class="prose">{recipe.unclassified}</div></section>{/if}
{/if}
{#if recipe.enrichment_status && recipe.enrichment_status !== 'complete'}<p class="no-print">Ingredient weights: {recipe.enrichment_status}{#if recipe.can_edit}<button onclick={async () => {try { await mutate(`/recipes/${id(recipeId)}/enrich`); status = 'Weight estimates queued.'; } catch(e) {error = message(e);}}}>Retry weight estimates</button>{/if}</p>{/if}
<section class="photos no-print"><h2>Photos</h2><Photos {recipeId} /></section>
</article>
<Modal bind:open={deleteOpen} title="Delete recipe"><p>This recipe will no longer appear in the collection.</p><div class="dialog-actions"><Button variant="ghost" onclick={() => deleteOpen = false}>Cancel</Button><Button variant="danger" disabled={deleting} onclick={removeRecipe}>{#if deleting}<Spinner label="Deleting recipe" size={16} />Deleting…{:else}Delete recipe{/if}</Button></div></Modal>
{:else if !error}<p role="status">Opening the recipe…</p>{/if}

<style>
  .byline,.controls-heading{display:flex;align-items:center;gap:.5rem}.byline .eyebrow{margin:0}.controls-heading{justify-content:space-between}.controls-heading h2{margin:0}.small-control{display:inline-flex;align-items:center;justify-content:center;min-height:32px;padding:.3rem .5rem;border:1px solid transparent;border-radius:.4rem;background:transparent;color:var(--ui-text);cursor:pointer}.small-control:hover,.small-control.selected{border-color:var(--ui-control-border);background:var(--ui-surface)}.scale-panel{margin-top:.5rem;padding:.65rem;border:1px solid var(--ui-control-border);border-radius:.5rem;background:var(--ui-surface-muted)}.scale-panel .toolbar{margin:.25rem 0}.scale-panel label{display:inline-flex;align-items:center;gap:.35rem}.scale-panel input{width:5.5rem}.gram-amount{text-decoration:underline dotted;text-underline-offset:.18em;cursor:help}.dialog-actions{display:flex;justify-content:flex-end;gap:.5rem;margin-top:1.25rem}.photos{margin-top:2rem}
</style>
