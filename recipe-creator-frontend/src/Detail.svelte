<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { request, mutate, message, id } from './api';
  import { load, save, safeUrl } from './local';
  import { gramEstimateText, gramText, ingredientAmount, ingredientText, quantity } from './recipe';
  import type { Ingredient, Recipe } from './types';
  import Photos from './Photos.svelte';
  import Button from './ui/Button.svelte';
  import Icon from './ui/Icon.svelte';
  import Tag from './ui/Tag.svelte';
  import Modal from './ui/Modal.svelte';
  import Spinner from './ui/Spinner.svelte';
  import Tooltip from './ui/Tooltip.svelte';
  import BackLink from './ui/BackLink.svelte';
  import Dropdown from './ui/Dropdown.svelte';
  import SegmentedTabs from './ui/SegmentedTabs.svelte';
  let { recipeId, navigate }: { recipeId: string; navigate: (path: string) => void } = $props();
  let recipe = $state<Recipe>(), error = $state(''), status = $state(''), scale = $state(1), scaleMode = $state<'preset' | 'custom'>('preset'), grams = $state(load('grams', false));
  let checked = $state<string[]>(untrack(() => load(`checked:${recipeId}`, []))), bookmarks = $state<string[]>(load('bookmarks', []));
  let awake = $state(false), deleting = $state(false), deleteOpen = $state(false), weightsBusy = $state(false);
  let lock: WakeLockSentinel | undefined;
  let alive = true;
  const lifetime = new AbortController();
  let refreshTimer: ReturnType<typeof setTimeout> | undefined;

  function scheduleRecipeRefresh(value: Recipe) {
    clearTimeout(refreshTimer);
    if (['pending', 'running', 'retry'].includes(value.enrichment_status)) {
      refreshTimer = setTimeout(() => void fetchRecipe(), 2000);
    }
  }
  async function fetchRecipe() { error = ''; try { const result = await request<Recipe>(`/recipes/${id(recipeId)}`, {signal: lifetime.signal}); if (alive) { recipe = result; scheduleRecipeRefresh(result); } } catch(e) { if (alive) error = message(e); } }
  onMount(() => { void fetchRecipe(); return () => { alive = false; clearTimeout(refreshTimer); lifetime.abort(); void lock?.release(); }; });
  $effect(() => { save('grams', grams); save(`checked:${recipeId}`, checked); save('bookmarks', bookmarks); });
  const factor = $derived(Number.isFinite(scale) && scale > 0 ? scale : 1);

  async function wake() { try { if (awake) { await lock?.release(); awake = false; } else if ('wakeLock' in navigator) { lock = await navigator.wakeLock.request('screen'); if (!alive) {await lock.release(); return;} awake = true; lock.addEventListener('release', () => awake = false); } else status = 'Keeping the screen awake is not supported by this browser.'; } catch { status = 'Could not keep the screen awake. Check your power-saving settings.'; } }
  async function share() { try { if (navigator.share) await navigator.share({title: recipe?.title, url: location.href}); else { await navigator.clipboard.writeText(location.href); status = 'Recipe link copied.'; } } catch(e) { if (!(e instanceof DOMException && e.name === 'AbortError')) status = `Copy this link: ${location.href}`; } }
  async function recalculateWeights() {
    weightsBusy = true; error = '';
    try {
      const result = await mutate<{enrichment_status?: string}>(`/recipes/${id(recipeId)}/enrich`);
      if (recipe) recipe = {...recipe, enrichment_status: result.enrichment_status || 'pending'};
      status = 'Weight estimates queued.';
    } catch(e) {
      error = message(e);
    } finally {
      weightsBusy = false;
    }
  }
  async function removeRecipe() { if (!recipe) return; deleting = true; error = ''; try { await mutate(`/recipes/${id(recipeId)}?expected_revision=${recipe.revision}`, undefined, 'DELETE', lifetime.signal); if (alive) navigate('/'); } catch(e) { if (alive) error = message(e); } finally { if (alive) { deleting = false; deleteOpen = false; } } }
  function openEditor(event: MouseEvent) {
    if (event.defaultPrevented || event.button > 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(`/recipes/${id(recipeId)}/edit`);
  }

  function gramExplanation(row: Ingredient) {
    const details = [`As written: ${row.original_text || ingredientText(row)}`];
    const estimate = gramEstimateText(row, factor);
    if (estimate) details.push(`${row.grams?.estimated === false ? 'Weight' : 'Estimate'}: ${estimate}`);
    const basis = typeof row.grams?.basis === 'string' ? row.grams.basis.trim() : '';
    if (row.grams?.estimated !== false && basis) details.push(`Basis: ${basis}`);
    const assumptions = row.grams?.assumptions;
    if (Array.isArray(assumptions)) {
      const values = assumptions.filter((value): value is string => typeof value === 'string' && Boolean(value.trim()));
      if (values.length) details.push(`Assumptions: ${values.join('; ')}`);
    }
    return details.join('\n');
  }
  function gramUnavailableExplanation(row: Ingredient) {
    const reason = typeof row.grams?.refusal_reason === 'string' ? row.grams.refusal_reason.trim() : '';
    return reason
      ? `No gram conversion: ${reason}`
      : 'No gram conversion is available yet. This amount remains in the original units.';
  }
  function scaledAmount(value: number | string) { return new Intl.NumberFormat('en', {maximumFractionDigits: 3}).format(Number(value) * factor); }
  function hasGramWeight(row: Ingredient) { return gramText(row) !== null; }
  function originalAmount(row: Ingredient) {
    if (!row.name) return '';
    return ingredientAmount(row, factor);
  }
  function ingredientTail(row: Ingredient) {
    return `${row.name}${row.preparation ? `, ${row.preparation}` : ''}${row.optional ? ' (optional)' : ''}`;
  }
  function scaleBadge(row: Ingredient) {
    return factor !== 1 && (!grams || !hasGramWeight(row)) && (!row.name || quantity(row.quantity) === null);
  }
</script>
{#if error}<p class="notice error" role="alert">{error} <button onclick={fetchRecipe}>Reload recipe</button></p>{/if}
{#if recipe}
<article class="recipe">
<BackLink href="/" label="Back to collection" />
<div class="byline"><p class="eyebrow">From {recipe.author_name || 'Unknown author'}</p></div>
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
  <button class="small-control" aria-label={awake ? 'Stop keeping screen awake' : 'Keep screen awake'} aria-pressed={awake} onclick={wake}><Icon name="sun" color="var(--ui-accent)" fill={awake ? 'var(--ui-accent)' : undefined} label={awake ? 'Screen staying awake' : 'Keep screen awake'} /></button>
  {#if recipe.can_edit}<Button variant="secondary" size="sm" href={`/recipes/${id(recipe.id)}/edit`} onclick={openEditor}><Icon name="edit" size={17} />Edit</Button><Button variant="ghost" size="sm" ariaLabel="Delete recipe" onclick={() => deleteOpen = true}><Icon name="trash" label="Delete recipe" /></Button>{/if}
</div>
{#if status}<p role="status" class="notice">{status}</p>{/if}
{#if recipe.mode === 'text'}<p class="notice">Shared as written. Ingredient scaling and gram tools are unavailable until this recipe is organized.</p>
<div class="prose recipe-body">{recipe.source_text}</div>{#if recipe.can_edit}<Button variant="secondary" size="sm" href={`/recipes/${id(recipe.id)}/edit`} onclick={openEditor}><Icon name="edit" size={16} />Organize ingredients</Button>{/if}
{:else}
<section class="cooking-controls no-print" aria-label="Cooking controls">
  <div class="controls-heading"><h2>Ingredients</h2>
    <Dropdown label="Ingredient settings" closeOnSelect={false}>
      {#snippet trigger(open)}
        <button class="small-control" type="button" title="Adjust servings and units" aria-label="Adjust ingredient scale" aria-haspopup="menu" aria-expanded={open}><Icon name="settings" label="Adjust ingredient scale" /></button>
      {/snippet}
      <div class="ingredient-settings">
        <div class="settings-group">
          <span class="settings-label">Scale</span>
          <SegmentedTabs ariaLabel="Scale" options={[{value:'1',label:'1×'},{value:'2',label:'2×'},{value:'3',label:'3×'},{value:'custom',label:'Custom'}]} value={scaleMode === 'custom' ? 'custom' : String(factor)} onchange={(value) => { if (value === 'custom') scaleMode = 'custom'; else { scaleMode = 'preset'; scale = Number(value); } }} />
          {#if scaleMode === 'custom'}<label>Custom multiplier<input type="number" min="0.01" max="1000" step="any" bind:value={scale}></label>{/if}
          {#if quantity(recipe.yield_amount)}<label>Servings<input type="number" min="0.01" step="any" value={quantity(recipe.yield_amount)! * factor} onchange={(event) => {const value = Number(event.currentTarget.value); if (value > 0) { scaleMode = 'custom'; scale = value / quantity(recipe!.yield_amount)!; }}}></label>{/if}
        </div>
        <div class="settings-group">
          <span class="settings-label">Units</span>
          <SegmentedTabs ariaLabel="Units" options={[{value:'original',label:'Original'},{value:'grams',label:'Grams'}]} value={grams ? 'grams' : 'original'} onchange={(value) => grams = value === 'grams'} />
        </div>
      </div>
    </Dropdown>
  </div>
</section>
<div class="recipe-columns">
<section><h2 class="sr-only">Ingredients</h2>{#each recipe.ingredient_groups as group}<section class="ingredients">{#if group.name && recipe.ingredient_groups.length > 1}<h3>{group.name}</h3>{/if}{#each group.ingredients as row}{@const weight = gramText(row,factor)}<div class:checked={checked.includes(row.id)}><label class="ingredient"><input type="checkbox" checked={checked.includes(row.id)} onchange={() => checked = checked.includes(row.id) ? checked.filter(value => value !== row.id) : [...checked, row.id]}>
  {#if scaleBadge(row)}<span class="scale-badge">{scaledAmount(1)}×</span>{/if}
  {#if grams && weight}
    <span>{row.grams?.estimated === false ? '' : '≈ '}<Tooltip text={gramExplanation(row)}><button type="button" class="gram-amount">{weight}</button></Tooltip>{' '}{ingredientTail(row) || row.original_text}</span>
  {:else if grams}
    <span><Tooltip text={gramUnavailableExplanation(row)}><button type="button" class="gram-amount unavailable">{originalAmount(row) || ingredientText(row,factor)}</button></Tooltip>{#if originalAmount(row)}{' '}{ingredientTail(row)}{/if}</span>
  {:else}
    <span>{ingredientText(row,factor)}</span>
  {/if}
</label></div>{/each}</section>{/each}</section>
<section><h2>Directions</h2><div class="prose">{recipe.directions}</div></section>
</div>
{#if recipe.notes}<section><h2>Notes</h2><div class="prose">{recipe.notes}</div></section>{/if}
{/if}
{#if recipe.enrichment_status && recipe.enrichment_status !== 'complete'}<p class="no-print">Ingredient weights: {recipe.enrichment_status}{#if recipe.can_edit} <button disabled={weightsBusy} onclick={recalculateWeights}>{weightsBusy ? 'Queueing…' : 'Retry weight estimates'}</button>{/if}</p>{:else if recipe.can_edit}<p class="no-print"><button disabled={weightsBusy} onclick={recalculateWeights}>{weightsBusy ? 'Queueing…' : 'Recalculate weight estimates'}</button></p>{/if}
<section class="photos no-print"><h2>Photos</h2><Photos {recipeId} /></section>
</article>
<Modal bind:open={deleteOpen} title="Delete recipe"><p>This recipe will no longer appear in the collection.</p><div class="dialog-actions"><Button variant="ghost" onclick={() => deleteOpen = false}>Cancel</Button><Button variant="danger" disabled={deleting} onclick={removeRecipe}>{#if deleting}<Spinner label="Deleting recipe" size={16} />Deleting…{:else}Delete recipe{/if}</Button></div></Modal>
{:else if !error}<p role="status">Opening the recipe…</p>{/if}

<style>
  .byline,.controls-heading{display:flex;align-items:center;gap:.5rem}.byline .eyebrow{margin:0}.controls-heading{justify-content:space-between}.controls-heading h2{margin:0}.small-control{display:inline-flex;align-items:center;justify-content:center;min-height:32px;padding:.3rem .5rem;border:1px solid transparent;border-radius:.4rem;background:transparent;color:var(--ui-text);cursor:pointer}.small-control:hover{border-color:var(--ui-control-border);background:var(--ui-surface)}.ingredient-settings{display:flex;flex-direction:column;gap:.8rem;min-width:18rem;padding:.35rem}.settings-group{display:flex;flex-direction:column;gap:.35rem}.settings-label{font-size:.8rem;font-weight:650;color:var(--muted)}.ingredient-settings label{display:flex;align-items:center;justify-content:space-between;gap:.5rem;font-size:.9rem}.ingredient-settings input{width:6rem;min-height:32px;padding:.25rem .4rem}.ingredient{align-items:center}.ingredient input{margin:0}.gram-amount{min-height:0;line-height:inherit}
</style>
