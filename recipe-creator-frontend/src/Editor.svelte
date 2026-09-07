<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { ApiError, request, mutate, session, message, id } from './api';
  import { load, save, remove, safeUrl } from './local';
  import { blank, ingredient, formatRecipe, applyParse, ParseGuard, move } from './recipe';
  import IdentityPrompt from './IdentityPrompt.svelte';
  import type { Recipe, RecipeDraft, ParseResult, Session } from './types';
  let { recipeId, navigate }: { recipeId?: string; navigate: (path: string) => void } = $props();
  type SavedDraft = {draft: RecipeDraft; revision?: number; key: string; undo?: RecipeDraft; saved: string};
  const storageKey = untrack(() => `draft:${recipeId || 'new'}`);
  let draft = $state<RecipeDraft>(blank(load('editor-mode', 'text'))), revision = $state<number>(), identity = $state<Session>();
  let ready = $state(false), allowed = $state(untrack(() => !recipeId)), busy = $state(false), parsing = $state(false), error = $state(''), notice = $state(''), persisted = $state('');
  let warnings = $state<string[]>([]), undo = $state<RecipeDraft>(), recovered = $state<SavedDraft | null>(null), needsName = $state(false), conflict = $state(false);
  let publishKey: string = crypto.randomUUID();
  let alive = true;
  const lifetime = new AbortController();
  let baseline = '', parseCache = new Map<string, ParseResult>();
  const guard = new ParseGuard();
  const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
  onMount(() => {
    void (async () => { try {
      identity = await session(); if (!alive) return;
      if (recipeId) { const recipe = await request<Recipe>(`/recipes/${id(recipeId)}`, {signal: lifetime.signal}); if (!alive) return; allowed = recipe.can_edit; revision = recipe.revision; draft = Object.fromEntries(Object.keys(blank()).map(key => [key, recipe[key as keyof Recipe] ?? blank()[key as keyof RecipeDraft]])) as unknown as RecipeDraft; draft.unclassified = recipe.unclassified || ''; }
      baseline = JSON.stringify(draft); recovered = load<SavedDraft | null>(storageKey, null); ready = true;
    } catch(e) {if (alive) error = message(e);} })();
    const leave = (event: BeforeUnloadEvent) => {if (JSON.stringify(draft) !== baseline) {event.preventDefault(); event.returnValue = '';}};
    window.addEventListener('beforeunload', leave);
    return () => {alive = false; lifetime.abort(); guard.cancel(); if (ready && allowed && !recovered && JSON.stringify(draft) !== baseline) save(storageKey, {draft: copy(draft), revision, key: publishKey, undo: undo ? copy(undo) : undefined, saved: new Date().toISOString()}); window.removeEventListener('beforeunload', leave);};
  });
  $effect(() => {
    const snapshot = JSON.stringify(draft); const previous = undo ? JSON.stringify(undo) : undefined;
    if (!ready || recovered || !allowed) return;
    const timer = setTimeout(() => {const ok = save(storageKey, {draft: JSON.parse(snapshot), revision, key: publishKey, undo: previous ? JSON.parse(previous) : undefined, saved: new Date().toISOString()}); persisted = ok ? 'Draft saved on this device.' : 'Local storage is unavailable. Keep this tab open or copy your text before leaving.';}, 350);
    return () => clearTimeout(timer);
  });
  function restore() { if (!recovered) return; draft = recovered.draft; revision = recovered.revision; publishKey = recovered.key || crypto.randomUUID(); undo = recovered.undo; recovered = null; notice = 'Recovered local draft. Publishing checks its original revision.'; }
  function cancelParse() { guard.cancel(); parsing = false; notice = 'Organization cancelled. Your recipe is unchanged.'; }
  async function switchMode() {
    error = ''; warnings = []; const snapshot = copy(draft); undo = snapshot;
    if (draft.mode === 'structured') { guard.cancel(); draft = {...draft, mode: 'text', source_text: formatRecipe(draft)}; save('editor-mode', 'text'); return; }
    if (!draft.source_text.trim()) {draft.mode = 'structured'; save('editor-mode', 'structured'); return;}
    const {version, signal} = guard.start(); parsing = true;
    const serialized = JSON.stringify(snapshot);
    try {
      const result = parseCache.get(snapshot.source_text) || await mutate<ParseResult>('/parse', {source_text: snapshot.source_text}, 'POST', signal);
      if (!guard.accepts(version) || JSON.stringify(draft) !== serialized) { if (guard.accepts(version)) notice = 'Draft changed while organizing. Result ignored; try again when ready.'; return; }
      if (result.source_text !== undefined && result.source_text !== snapshot.source_text) throw new Error('The organizer returned a different source. Result ignored.');
      parseCache.set(snapshot.source_text, result); draft = applyParse(snapshot, result); warnings = result.warnings || []; save('editor-mode', 'structured'); notice = 'Organized for review. Original source is retained; directions and notes should read exactly as written.';
    } catch(e) { if (guard.accepts(version)) error = `${message(e)} Your text is safe; you can publish in Text mode or try again.`; }
    finally { if (guard.accepts(version)) parsing = false; }
  }
  async function publish() {
    error = ''; conflict = false;
    if (!identity?.user) {needsName = true; return;}
    if (draft.source_url && !safeUrl(draft.source_url)) {error = 'The source link must begin with http:// or https://.'; return;}
    busy = true; guard.cancel(); parsing = false;
    try {
      const payload = Object.fromEntries([...Object.keys(blank()), 'unclassified'].map(key => [key, draft[key as keyof RecipeDraft]]));
      const result = await mutate<Recipe>(recipeId ? `/recipes/${id(recipeId)}` : '/recipes', {...payload, ...(recipeId ? {expected_revision: revision} : {})}, recipeId ? 'PUT' : 'POST', lifetime.signal, recipeId ? undefined : publishKey);
      if (!alive) return;
      baseline = JSON.stringify(draft); ready = false; remove(storageKey); navigate(`/recipes/${id(result.id)}`);
    } catch(e) {if (alive) {error = message(e); conflict = e instanceof ApiError && e.status === 409;}} finally {if (alive) busy = false;}
  }
  function invalidate(row: ReturnType<typeof ingredient>) { row.grams = null; }
</script>
<a class="back" href={recipeId ? `/recipes/${id(recipeId)}` : '/'}>← Back without publishing</a><p class="eyebrow">Pass something good along</p><h1>{recipeId ? 'Edit recipe' : 'Add a recipe'}</h1>
{#if error}<p class="notice error" role="alert">{error}</p>{/if}
{#if conflict}<div class="notice" role="alert"><h2>A newer version exists</h2><p>Your local draft is safe. Open the current recipe in another tab and compare before starting a fresh edit. We will never overwrite the other version automatically.</p><a target="_blank" rel="noopener" href={`/recipes/${id(recipeId!)}`}>Compare current recipe ↗</a></div>{/if}
{#if !ready && !error}<p role="status">Opening the editor…</p>{:else if ready && !allowed}<p>You can read this recipe, but only its owner or an administrator can edit it.</p>{:else if ready}
{#if recovered}<section class="notice"><h2>A saved draft is waiting</h2><p>Saved {new Date(recovered.saved).toLocaleString()}. Recover it before editing, or keep the server version.</p><button onclick={restore}>Recover draft</button><button onclick={() => { recovered = null; remove(storageKey); }}>Discard local draft</button></section>{/if}
{#if needsName && !identity?.user}<IdentityPrompt onready={(value) => {identity = value; needsName = false; notice = 'Profile ready. Review your recipe, then publish.';}} />{/if}
<form onsubmit={(event) => {event.preventDefault(); void publish();}}>
<fieldset disabled={busy || !!recovered}><label>Recipe title<input required maxlength="300" bind:value={draft.title} placeholder="What are we making?"></label>
<div class="toolbar"><span class="mode-label">{draft.mode === 'text' ? 'Text mode' : 'Structured mode'}</span><button type="button" disabled={parsing} onclick={switchMode}>Switch to {draft.mode === 'text' ? 'Structured' : 'Text'}</button>{#if parsing}<button type="button" onclick={cancelParse}>Cancel organizing</button><span role="status">Organizing ingredients…</span>{/if}{#if undo}<button type="button" onclick={() => {guard.cancel(); parsing = false; draft = copy(undo!); undo = undefined; save('editor-mode', draft.mode); notice = 'Mode switch undone.';}}>Undo mode switch</button>{/if}</div>
{#if draft.mode === 'text'}<label>Recipe body<textarea class="source-editor" rows="20" bind:value={draft.source_text} placeholder="A little introduction…&#10;&#10;Ingredients&#10;=== For the sauce ===&#10;…&#10;&#10;Directions&#10;…&#10;&#10;Notes&#10;…"></textarea></label><p class="help">Write it your way. Text recipes can be published even when AI is unavailable. Switching to Structured sends this text to our configured AI provider to organize ingredients, not rewrite your prose.</p>
{:else}
<label>Description<textarea rows="3" bind:value={draft.description}></textarea></label><h2>Ingredient sections</h2>
{#each draft.ingredient_groups as group, gi (group.id)}<section class="editor-group"><div class="toolbar"><label>Section name (optional)<input bind:value={group.name}></label><button type="button" aria-label={`Move section ${gi + 1} up`} disabled={gi === 0} onclick={() => draft.ingredient_groups = move(draft.ingredient_groups, gi, -1)}>↑</button><button type="button" aria-label={`Move section ${gi + 1} down`} disabled={gi === draft.ingredient_groups.length - 1} onclick={() => draft.ingredient_groups = move(draft.ingredient_groups, gi, 1)}>↓</button><button type="button" onclick={() => {if (confirm('Remove this ingredient section?')) draft.ingredient_groups = draft.ingredient_groups.filter(g => g.id !== group.id);}}>Remove section</button></div>
{#each group.ingredients as row, ri (row.id)}<fieldset class="ingredient-row"><legend>Ingredient {ri + 1}</legend><div class="row-fields"><label>Quantity<input bind:value={row.quantity} oninput={() => invalidate(row)} placeholder="½ or unknown"></label><label>Range maximum<input bind:value={row.quantity_max} oninput={() => invalidate(row)}></label><label>Unit<input bind:value={row.unit} oninput={() => invalidate(row)}></label><label>Ingredient name<input bind:value={row.name} oninput={() => invalidate(row)}></label><label>Preparation<input bind:value={row.preparation} oninput={() => invalidate(row)}></label></div><label>Original text / fallback<input bind:value={row.original_text} oninput={() => invalidate(row)}></label><div class="toolbar"><label class="inline"><input type="checkbox" bind:checked={row.optional} onchange={() => invalidate(row)}>Optional</label><button type="button" aria-label={`Move ingredient ${ri + 1} up`} disabled={ri === 0} onclick={() => group.ingredients = move(group.ingredients, ri, -1)}>↑</button><button type="button" aria-label={`Move ingredient ${ri + 1} down`} disabled={ri === group.ingredients.length - 1} onclick={() => group.ingredients = move(group.ingredients, ri, 1)}>↓</button><button type="button" onclick={() => group.ingredients = group.ingredients.filter(i => i.id !== row.id)}>Remove ingredient</button></div></fieldset>{/each}
<button type="button" onclick={() => group.ingredients = [...group.ingredients, ingredient()]}>Add ingredient</button></section>{/each}
<button type="button" onclick={() => draft.ingredient_groups = [...draft.ingredient_groups, {id: crypto.randomUUID(), name: '', ingredients: [ingredient()]}]}>Add section</button>
<label>Directions<textarea rows="10" bind:value={draft.directions}></textarea></label><label>Notes<textarea rows="5" bind:value={draft.notes}></textarea></label>{#if draft.unclassified}<label>Unclassified original text — review, do not lose<textarea rows="5" bind:value={draft.unclassified}></textarea></label>{/if}
{/if}
<details class="metadata"><summary>Optional details: tags, yield, source & modifications</summary><label>Tags (comma-separated)<input value={draft.tags.join(', ')} onchange={(event) => draft.tags = event.currentTarget.value.split(',').map(tag => tag.trim()).filter(Boolean)}></label><div class="row-fields"><label>Yield amount<input bind:value={draft.yield_amount} placeholder="4"></label><label>Yield unit<input bind:value={draft.yield_unit} placeholder="servings"></label></div><label>Original source URL<input type="url" bind:value={draft.source_url}></label><label>Your modifications<textarea rows="4" bind:value={draft.modifications}></textarea></label></details>
{#if draft.mode === 'structured'}<details><summary>Inline text preview & retained source</summary><h3>Current structured recipe</h3><pre class="prose">{formatRecipe(draft)}</pre><h3>Retained source before organizing</h3><pre class="prose">{draft.source_text}</pre></details>{/if}
{#each warnings as warning}<p class="notice">{warning}</p>{/each}{#if notice}<p role="status" class="notice">{notice}</p>{/if}<p class="help" role="status">{busy ? 'Publishing…' : persisted}</p>
<button class="primary" disabled={busy || parsing || !draft.title.trim()}>{busy ? 'Publishing…' : recipeId ? 'Save changes' : 'Publish recipe'}</button>
</fieldset></form>{/if}
