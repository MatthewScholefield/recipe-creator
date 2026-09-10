<script lang="ts">
  import { onMount, tick, untrack } from 'svelte';
  import { ApiError, request, mutate, message, id } from './api';
  import { save, remove, safeUrl } from './local';
  import { appState, refreshIdentity } from './app-state.svelte';
  import { blank, ingredient, formatRecipe, applyParse, ParseGuard, move, ingredientLine, changedIngredient,
    dirtyIngredientLines, applyIngredientLines, withoutEmptyIngredients, normalizeTags, tagError, MEAL_CLASSIFIERS } from './recipe';
  import { createDraft, saveDraft, readDraft, listDrafts, deleteDraft, draftHref, subscribeDrafts,
    migrateLegacyDraft, readLegacyDraft, recoverLegacyDraft, type LocalDraft, type LegacyDraft, type DraftSummary } from './drafts';
  import IdentityPrompt from './IdentityPrompt.svelte';
  import TagPicker from './ui/TagPicker.svelte';
  import Tooltip from './ui/Tooltip.svelte';
  import Spinner from './ui/Spinner.svelte';
  import Icon from './ui/Icon.svelte';
  import Button from './ui/Button.svelte';
  import BackLink from './ui/BackLink.svelte';
  import type { Recipe, RecipeDraft, ParseResult, IngredientGroup, IngredientLinesResult, TagCatalog } from './types';

  let { recipeId, draftId, navigate }: {recipeId?: string; draftId?: string; navigate: (path: string, options?: {replace?: boolean}) => void} = $props();
  const storageKey = untrack(() => `draft:${recipeId || 'new'}`);
  const helpId = `publish-help-${crypto.randomUUID()}`;
  const copy = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
  let draft = $state<RecipeDraft>(blank('text')), revision = $state<number>(), undo = $state<RecipeDraft>();
  let dirty = $state<Record<string, string>>({}), undoLines = $state<Record<string, string>>({});
  let ready = $state(false), allowed = $state(untrack(() => !recipeId)), busy = $state(false), parsing = $state(false);
  let error = $state(''), notice = $state(''), persisted = $state(''), conflict = $state(false), external = $state(false);
  let activeId = $state(''), draftName = $state(''), naming = $state(false), textChoice = $state(false), discard = $state(false);
  let drafts = $state<DraftSummary[]>([]), legacyAvailable = $state(false), recovered = $state<LegacyDraft | null>(null);
  let tags = $state<string[]>([...MEAL_CLASSIFIERS]), tagsError = $state('');
  let publishKey: string = crypto.randomUUID(), baseline = '', lastSaved: string | null = null, alive = true, writing = false;
  const guard = new ParseGuard(), lifetime = new AbortController();
  const inputs = new Map<string, HTMLInputElement>();
  let hasIdentity = $derived(appState.identity?.user?.state === 'active');
  let validation = $derived(tagError(draft.tags));
  let editing = $derived(!!recipeId || !!activeId);
  let submitHelp = $derived(!hasIdentity ? `Set your name to ${recipeId ? 'save changes' : 'publish'}` :
    external ? 'Reload or save as a new draft before publishing.' : validation || (!draft.title.trim() ? 'Add a recipe title.' : busy ? 'Saving your recipe…' : parsing ? 'Wait for organization or cancel it.' : 'Ready to save.'));
  let submitDisabled = $derived(!hasIdentity || !ready || !allowed || busy || parsing || !!recovered || external || !draft.title.trim() || !!validation);

  function resume(value: LocalDraft) {
    guard.cancel(); parsing = false; busy = false;
    activeId = value.id; draftName = value.name; draft = copy(value.draft); undo = value.undo ? copy(value.undo) : undefined;
    dirty = copy(value.ingredientLines || {}); undoLines = {}; publishKey = value.publishKey;
    baseline = JSON.stringify(draft); lastSaved = JSON.stringify(readDraft(value.id)); external = false; error = '';
  }
  function flush() {
    if (!ready || !allowed || !editing || recovered || external) return false;
    if (activeId && !writing && JSON.stringify(readDraft(activeId)) !== lastSaved) {
      external = true; cancelWork(); return false;
    }
    const value = {draft: copy(draft), undo: undo ? copy(undo) : undefined, ingredientLines: copy(dirty)};
    let ok: boolean;
    writing = true;
    if (activeId) {
      const local: LocalDraft = {...value, version: 2, id: activeId, name: draftName.trim().slice(0, 120) || 'Untitled recipe', updatedAt: new Date().toISOString(), publishKey};
      ok = saveDraft(local); if (ok) lastSaved = JSON.stringify(local);
    } else ok = save(storageKey, {...value, revision, key: publishKey, saved: new Date().toISOString()});
    writing = false;
    persisted = ok ? 'Draft saved on this device.' : 'Local storage is unavailable. Keep this tab open or copy your recipe before leaving.';
    return ok;
  }
  async function startNew() {
    if (busy) return;
    flush(); const value = createDraft(); resume(value); ready = true;
    const ok = flush(); await tick();
    if (ok && alive) navigate(draftHref(value.id), {replace: true});
  }
  async function fork() {
    const snapshot = copy(draft), previous = undo ? copy(undo) : undefined, lines = copy(dirty);
    const value = createDraft(draftName || draft.title);
    resume({...value, draft: snapshot, undo: previous, ingredientLines: lines});
    const ok = flush(); await tick();
    if (ok && alive) navigate(draftHref(value.id), {replace: true});
  }
  function reloadLocal() {
    const value = readDraft(activeId);
    if (value) resume(value); else error = 'This draft was removed in another tab. Save as a new draft to keep your copy.';
  }
  async function recoverOldNew() {
    const value = recoverLegacyDraft();
    if (!value) { error = 'The old draft could not be recovered. Its stored copy has not been removed.'; return; }
    resume(value); legacyAvailable = false; await tick(); if (alive) navigate(draftHref(value.id), {replace: true});
  }
  function restoreEdit() {
    if (!recovered) return;
    draft = copy(recovered.draft); revision = recovered.revision; publishKey = recovered.key;
    undo = recovered.undo ? copy(recovered.undo) : undefined; undoLines = {}; dirty = copy(recovered.ingredientLines || {}); recovered = null;
    notice = 'Recovered local draft. Saving checks its original revision.';
  }
  async function fetchTags() {
    try { const catalog = await request<TagCatalog>('/tags', {signal: lifetime.signal}); if (alive) { tags = normalizeTags([...MEAL_CLASSIFIERS, ...catalog.tags]); tagsError = ''; } }
    catch (e) { if (alive) tagsError = `Tags could not be loaded. ${message(e)}`; }
  }
  onMount(() => {
    void fetchTags();
    void refreshIdentity().catch(e => { if (alive) notice = `You can keep writing. ${message(e)}`; });
    void (async () => {
      try {
        if (recipeId) {
          const recipe = await request<Recipe>(`/recipes/${id(recipeId)}`, {signal: lifetime.signal}); if (!alive) return;
          allowed = recipe.can_edit; revision = recipe.revision;
          draft = Object.fromEntries(Object.keys(blank()).map(key => [key, recipe[key as keyof Recipe] ?? blank()[key as keyof RecipeDraft]])) as unknown as RecipeDraft;
          recovered = readLegacyDraft(recipeId); baseline = JSON.stringify(draft);
        } else {
          const migrated = migrateLegacyDraft(); legacyAvailable = !migrated && !!readLegacyDraft();
          if (!migrated && !legacyAvailable) notice = 'An old draft could not be read. Its stored copy has been kept.';
          drafts = listDrafts();
          if (draftId) { const value = readDraft(draftId); if (value) resume(value); else error = 'This draft is missing or unreadable. Start a new recipe, or return to your drafts.'; }
        }
        ready = true;
      } catch (e) { if (alive) error = message(e); }
    })();
    const unsubscribe = subscribeDrafts(() => {
      drafts = listDrafts();
      if (activeId && !writing && JSON.stringify(readDraft(activeId)) !== lastSaved) { external = true; cancelWork(); }
    });
    const leave = (event: BeforeUnloadEvent) => {
      if (editing && JSON.stringify(draft) !== baseline) { flush(); event.preventDefault(); event.returnValue = ''; }
    };
    window.addEventListener('beforeunload', leave);
    return () => { flush(); alive = false; lifetime.abort(); guard.cancel(); unsubscribe(); window.removeEventListener('beforeunload', leave); };
  });
  $effect(() => {
    // Only content dependencies schedule autosave; timestamps/status must not make a loop.
    JSON.stringify(draft); JSON.stringify(undo); JSON.stringify(dirty); draftName;
    if (!ready || !allowed || !editing || recovered || external) return;
    const timer = setTimeout(flush, 350); return () => clearTimeout(timer);
  });
  function cancelWork() { guard.cancel(); parsing = false; busy = false; }
  function changed() {
    if (parsing) { cancelWork(); notice = 'Recipe changed while organizing. Result ignored; organize again when ready.'; }
  }
  function cancelParse() { cancelWork(); notice = 'Organization cancelled. Your text is retained.'; }
  function setLine(group: IngredientGroup, index: number, text: string) {
    changed(); const row = group.ingredients[index];
    group.ingredients[index] = changedIngredient(row, text); dirty = {...dirty, [row.id]: text};
  }
  async function addRow(group: IngredientGroup, after = group.ingredients.length - 1) {
    changed(); const row = ingredient(); group.ingredients = [...group.ingredients.slice(0, after + 1), row, ...group.ingredients.slice(after + 1)];
    await tick(); if (alive) inputs.get(row.id)?.focus();
  }
  function rowKeys(event: KeyboardEvent, group: IngredientGroup, index: number) {
    if (event.key === 'Enter' && !event.isComposing && event.keyCode !== 229) { event.preventDefault(); void addRow(group, index); }
  }
  function removeRow(group: IngredientGroup, index: number) {
    changed(); const row = group.ingredients[index]; const next = {...dirty}; delete next[row.id]; dirty = next;
    group.ingredients = group.ingredients.filter((_, i) => i !== index);
  }
  function bindInput(node: HTMLInputElement, rowId: string) { inputs.set(rowId, node); return {destroy() { inputs.delete(rowId); }}; }
  function undoOrganization() {
    if (!undo) return;
    cancelWork(); draft = copy(undo); dirty = copy(undoLines); undo = undefined; undoLines = {}; textChoice = false;
  }
  function editText(formatted: boolean) {
    cancelWork(); undo = copy(draft); undoLines = copy(dirty);
    draft = {...draft, mode: 'text', source_text: formatted ? formatRecipe(draft) : draft.source_text}; textChoice = false;
  }
  async function previewLines(version: number, signal: AbortSignal): Promise<void> {
    const snapshot = copy(draft), serialized = JSON.stringify(snapshot), lines = dirtyIngredientLines(snapshot, dirty);
    if (!lines.length) return;
    const result = await mutate<IngredientLinesResult>('/ingredients/parse', {lines}, 'POST', signal);
    if (!alive || !guard.accepts(version) || JSON.stringify(draft) !== serialized) return;
    draft = applyIngredientLines(snapshot, result.items.map(item => ({...item, ingredient: {...item.ingredient,
      quantity: item.ingredient.quantity ?? null, quantity_max: item.ingredient.quantity_max ?? null, grams: item.ingredient.grams ?? null}})));
    const remaining = {...dirty};
    for (const item of result.items) delete remaining[item.id];
    dirty = remaining;
  }
  async function organizeIngredients() {
    if (busy || parsing) return;
    error = ''; notice = ''; const {version, signal} = guard.start(); parsing = true;
    try { await previewLines(version, signal); }
    catch (e) { if (guard.accepts(version)) error = message(e); }
    finally { if (guard.accepts(version)) parsing = false; }
  }
  async function organize() {
    if (busy || parsing || !draft.source_text.trim()) return;
    error = ''; const snapshot = copy(draft), serialized = JSON.stringify(snapshot);
    const {version, signal} = guard.start(); parsing = true;
    try {
      const result = await mutate<ParseResult>('/parse', {source_text: snapshot.source_text}, 'POST', signal);
      if (!alive || !guard.accepts(version) || JSON.stringify(draft) !== serialized) return;
      undo = snapshot; undoLines = copy(dirty); draft = applyParse(snapshot, result); dirty = {};
    } catch (e) { if (guard.accepts(version)) error = `${message(e)} Your text is safe; publish as written or try again.`; }
    finally { if (guard.accepts(version)) parsing = false; }
  }
  async function publish() {
    if (submitDisabled) return;
    error = ''; conflict = false;
    if (draft.source_url && !safeUrl(draft.source_url)) { error = 'The source link must begin with http:// or https://.'; return; }
    flush(); if (external) return;
    const {version, signal} = guard.start(); busy = true;
    try {
      if (draft.mode === 'structured') {
        try { await previewLines(version, signal); }
        catch (e) { if (guard.accepts(version)) error = message(e); return; }
      }
      if (!alive || !guard.accepts(version) || !hasIdentity) return;
      let snapshot = copy(draft);
      snapshot = withoutEmptyIngredients(snapshot); snapshot.tags = normalizeTags(snapshot.tags);
      const payload = Object.fromEntries(Object.keys(blank()).map(key => [key, snapshot[key as keyof RecipeDraft]]));
      const result = await mutate<Recipe>(recipeId ? `/recipes/${id(recipeId)}` : '/recipes', {...payload, ...(recipeId ? {expected_revision: revision} : {})}, recipeId ? 'PUT' : 'POST', signal, recipeId ? undefined : publishKey);
      if (!alive || !guard.accepts(version)) return;
      baseline = JSON.stringify(draft); ready = false;
      if (activeId) deleteDraft(activeId); else remove(storageKey);
      navigate(`/recipes/${id(result.id)}`);
    } catch (e) {
      if (alive && guard.accepts(version)) {
        error = message(e); conflict = e instanceof ApiError && e.status === 409;
        if (e instanceof ApiError && [401, 403].includes(e.status)) {
          appState.identity = null;
          try { await refreshIdentity(); } catch { /* Remain anonymous until identity can be checked. */ }
          if (recipeId) allowed = false;
        }
      }
    } finally { if (alive && guard.accepts(version)) { busy = false; flush(); } }
  }
  function discardDraft() {
    cancelWork(); ready = false;
    if (!deleteDraft(activeId)) { ready = true; error = 'Could not discard this draft. Please try again.'; return; }
    navigate('/new', {replace: true});
  }
</script>

<BackLink href={recipeId ? `/recipes/${id(recipeId)}` : '/'} label={recipeId ? 'Back to recipe' : 'Back to recipes'} />
<h1>{recipeId ? 'Edit recipe' : 'Add a recipe'}</h1>
{#if error}<p class="notice error" role="alert">{error}</p>{/if}
{#if conflict}<section class="notice" role="alert"><h2>A newer version exists</h2><p>Your local draft is safe. Compare the current recipe before starting a fresh edit.</p><Button variant="secondary" size="sm" href={`/recipes/${id(recipeId!)}`} target="_blank" rel="noopener"><Icon name="git-compare" size={16} />Compare current recipe</Button></section>{/if}
{#if !ready && !error}<Spinner label="Opening the editor…" />
{:else if ready && !allowed}<p>You can read this recipe, but only its owner or an administrator can edit it. Your local draft has been kept.</p>
{:else if ready}
  {#if !recipeId && !editing}
    <section aria-label="Drafts">
      <button class="primary" onclick={startNew}><Icon name="plus" size={18} />Start a new recipe</button>
      {#if drafts.length}<h2>Drafts</h2>
        <ul class="draft-list">{#each drafts as item (item.id)}<li><span><strong>{item.name}</strong><small>{new Date(item.updatedAt).toLocaleString()}</small></span><Button variant="secondary" size="sm" href={draftHref(item.id)}><Icon name="edit" size={16} />Resume<span class="sr-only"> {item.name}</span></Button></li>{/each}</ul>
      {/if}
      {#if legacyAvailable}<p>An older draft also needs recovery. Your other drafts are unchanged.</p><button onclick={recoverOldNew}>Recover older draft as new</button>{/if}
    </section>
  {:else}
    {#if external}<section class="notice" role="alert"><h2>Changed in another tab</h2><p>Autosave is paused. Keep your copy as a new draft, or reload the stored version.</p><button onclick={reloadLocal}>Reload draft</button><button onclick={fork}>Save as new draft</button></section>{/if}
    {#if activeId}<div class="toolbar draft-actions"><span class="draft-badge">Draft · {draftName}</span><button type="button" class="ghost" disabled={busy || external} onclick={() => naming = !naming}><Icon name="edit" size={16} />Rename</button><button type="button" class="ghost" disabled={busy} onclick={() => discard = !discard}><Icon name="trash" size={16} />Discard</button><Button variant="ghost" size="sm" href="/new"><Icon name="plus" size={16} />Start another recipe</Button></div>
      {#if naming}<label>Draft name<input maxlength="120" bind:value={draftName} disabled={busy || external}></label><button onclick={() => { draftName = draftName.trim() || 'Untitled recipe'; flush(); naming = false; }}>Done</button>{/if}
      {#if discard}<section class="notice"><p>Discard “{draftName}” from this device?</p><button onclick={discardDraft}>Discard draft</button><button onclick={() => discard = false}>Keep draft</button></section>{/if}
    {/if}
    {#if recovered}<section class="notice"><h2>A saved edit is waiting</h2><p>Saved {new Date(recovered.saved).toLocaleString()}.</p><button onclick={restoreEdit}>Recover draft</button><button onclick={() => { recovered = null; remove(storageKey); }}>Use server version</button></section>{/if}
    <form onsubmit={(event) => {event.preventDefault(); void publish();}} oninput={changed}>
      <fieldset disabled={busy || !!recovered}>
        <label>Recipe title<input required maxlength="300" bind:value={draft.title}></label>
        {#if draft.mode === 'text'}
          <label>Paste or write your recipe<textarea class="source-editor" rows="20" maxlength="100000" bind:value={draft.source_text} placeholder="Ingredients, directions, and anything else you want to share"></textarea></label>
          <div class="toolbar"><button type="button" disabled={parsing || !draft.source_text.trim()} onclick={organize}>Organize</button><span class="help">Optional: organize ingredients without rewriting your words.</span></div>
        {:else}
          <div class="toolbar"><button type="button" class="ghost" onclick={() => textChoice = !textChoice}><Icon name="edit" size={16} />Edit as text</button></div>
          {#if textChoice}<section class="notice"><p>Choose the text to edit. The current version is kept for undo.</p><button type="button" onclick={() => editText(false)}>Use original text</button><button type="button" onclick={() => editText(true)}>Use formatted current recipe</button></section>{/if}
          <label>Description<textarea rows="3" bind:value={draft.description}></textarea></label>
          <h2>Ingredients</h2>
          {#each draft.ingredient_groups as group, gi (group.id)}
            <section class="editor-group"><div class="toolbar"><label>Section name (optional)<input bind:value={group.name}></label>
              <button type="button" class="ghost" aria-label={`Move section ${gi + 1} up`} disabled={gi === 0} onclick={() => {changed(); draft.ingredient_groups = move(draft.ingredient_groups, gi, -1);}}>↑</button>
              <button type="button" class="ghost" aria-label={`Move section ${gi + 1} down`} disabled={gi === draft.ingredient_groups.length - 1} onclick={() => {changed(); draft.ingredient_groups = move(draft.ingredient_groups, gi, 1);}}>↓</button>
              <button type="button" class="ghost" aria-label={`Remove section ${gi + 1}`} onclick={() => {changed(); draft.ingredient_groups = draft.ingredient_groups.filter((_, index) => index !== gi);}}><Icon name="trash" size={16} /></button>
            </div>
            {#each group.ingredients as row, ri (row.id)}
              <div class="line-row"><label><span class="sr-only">Ingredient {gi + 1}.{ri + 1}</span><input use:bindInput={row.id} value={ingredientLine(row)} maxlength="10000" placeholder="2 tbsp milk" oninput={(event) => setLine(group, ri, event.currentTarget.value)} onkeydown={(event) => rowKeys(event, group, ri)}></label>
                <button type="button" class="ghost" aria-label={`Move ingredient ${gi + 1}.${ri + 1} up`} disabled={ri === 0} onclick={() => {changed(); group.ingredients = move(group.ingredients, ri, -1);}}>↑</button>
                <button type="button" class="ghost" aria-label={`Move ingredient ${gi + 1}.${ri + 1} down`} disabled={ri === group.ingredients.length - 1} onclick={() => {changed(); group.ingredients = move(group.ingredients, ri, 1);}}>↓</button>
                <button type="button" class="ghost" aria-label={`Remove ingredient ${gi + 1}.${ri + 1}`} onclick={() => removeRow(group, ri)}><Icon name="x" size={16} /></button>
              </div>
            {/each}
            <button type="button" class="ghost" onclick={() => addRow(group)}><Icon name="plus" size={16} />Add ingredient</button>
            </section>
          {/each}
          <div class="toolbar"><button type="button" class="ghost" onclick={() => {changed(); draft.ingredient_groups = [...draft.ingredient_groups, {id: crypto.randomUUID(), name: '', ingredients: [ingredient()]}];}}><Icon name="plus" size={16} />Add section</button><button type="button" disabled={parsing || !dirtyIngredientLines(draft, dirty).length} onclick={organizeIngredients}>Organize ingredients</button></div>
          <label>Directions<textarea rows="10" bind:value={draft.directions}></textarea></label><label>Notes<textarea rows="5" bind:value={draft.notes}></textarea></label>
        {/if}
        {#if undo}<button type="button" class="ghost" onclick={undoOrganization}>Undo organization</button>{/if}
        <TagPicker {tags} bind:selected={draft.tags} onchange={(values) => {changed(); draft.tags = normalizeTags(values);}} allowCreate={true} />
        {#if tagsError}<p class="help">{tagsError} <button type="button" class="ghost" onclick={fetchTags}>Retry tags</button></p>{/if}
        {#if validation}<p class="notice error" role="alert">{validation}</p>{/if}
        <details class="metadata"><summary>Optional details</summary>
          <div class="row-fields"><label>Yield amount<input bind:value={draft.yield_amount} placeholder="4"></label><label>Yield unit<input bind:value={draft.yield_unit} placeholder="servings"></label></div>
          <label>Source link<input type="url" bind:value={draft.source_url} placeholder="https://…"></label><label>Modifications<textarea rows="3" bind:value={draft.modifications}></textarea></label>
        </details>
        {#if draft.mode === 'structured'}<details><summary>Preview and original text</summary><pre class="prose">{formatRecipe(draft)}</pre><h3>Original text</h3><pre class="prose">{draft.source_text}</pre></details>{/if}
      </fieldset>
      {#if parsing}<div class="toolbar"><Spinner label="Organizing…" /><button type="button" onclick={cancelParse}>Cancel organizing</button></div>{/if}
      {#if busy}<div class="toolbar"><Spinner label="Saving recipe…" /><button type="button" onclick={() => {cancelWork(); notice = 'Save cancelled. Your draft is retained; retry uses the same publishing key.';}}>Cancel saving</button></div>{/if}
      {#if notice}<p role="status" class="notice">{notice}</p>{/if}
      <p class="help" role="status">{persisted}</p>
      {#if !hasIdentity}<IdentityPrompt onready={(value) => {appState.identity = value; notice = 'Name set. Review your recipe, then publish.';}} />{/if}
      <p class="help" id={helpId}>{submitHelp}</p>
      <!-- The focusable group exposes help for the disabled child button. -->
      <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
      <Tooltip text={submitHelp}><span class="submit-wrapper" role="group" tabindex={submitDisabled ? 0 : -1} aria-label={submitHelp} aria-describedby={helpId}><button class="primary" type="submit" disabled={submitDisabled} aria-describedby={helpId}>{recipeId ? 'Save changes' : 'Publish recipe'}</button></span></Tooltip>
    </form>
  {/if}
{/if}

<style>
  .line-row{display:flex;align-items:center;gap:.25rem;margin:.4rem 0}.line-row label{flex:1;margin:0;min-width:0}.line-row input{width:100%}.ghost{display:inline-flex;align-items:center;gap:.3rem;background:transparent;border-color:transparent;padding:.35rem .5rem;font-size:.9rem}.toolbar{flex-wrap:wrap}.draft-actions{margin-bottom:1rem}.draft-badge{font-size:.85rem;color:var(--muted)}.draft-list{list-style:none;padding:0}.draft-list li{display:flex;justify-content:space-between;gap:1rem;padding:.8rem 0;border-bottom:1px solid var(--border)}.draft-list small{display:block}.submit-wrapper{display:inline-flex}.submit-wrapper:focus-visible{outline:2px solid currentColor;outline-offset:4px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
  form input:not([type=checkbox]),form textarea{font-weight:400}
</style>
