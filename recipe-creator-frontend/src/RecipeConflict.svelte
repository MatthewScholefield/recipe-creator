<script lang="ts">
  import { onMount, tick } from 'svelte';
  import Button from './ui/Button.svelte';
  import Spinner from './ui/Spinner.svelte';
  import { recipeDraftFromRecipe } from './recipe';
  import { recipeDiffSections, recipeTextDiff, type RecipeDiffHunk } from './recipe-diff';
  import type { Recipe, RecipeDraft } from './types';

  type Base = {revision: number; draft: RecipeDraft};
  type Status = 'loading' | 'ready' | 'error';
  type Comparison = {key: keyof RecipeDraft; label: string; left: RecipeDiffHunk[]; right: RecipeDiffHunk[]};

  let {base, candidate, latest, status, error, busy, onretry, onreplace, ondiscard, onback}: {
    base?: Base;
    candidate: RecipeDraft;
    latest?: Recipe;
    status: Status;
    error: string;
    busy: boolean;
    onretry: () => void;
    onreplace: () => void;
    ondiscard: () => void;
    onback: () => void;
  } = $props();

  let heading: HTMLHeadingElement;
  let showUnchanged = $state(false);
  let confirmation = $state<'replace' | 'discard' | null>(null);
  const comparison = $derived.by<Comparison[]>(() => {
    if (!latest) return [];
    const candidateSections = recipeDiffSections(candidate);
    const latestSections = recipeDiffSections(recipeDraftFromRecipe(latest));
    if (!base) return candidateSections.map((section, index) => ({
      key: section.key,
      label: section.label,
      left: recipeTextDiff(latestSections[index].text, section.text),
      right: [],
    }));
    const baseSections = recipeDiffSections(base.draft);
    return candidateSections.map((section, index) => ({
      key: section.key,
      label: section.label,
      left: recipeTextDiff(baseSections[index].text, section.text),
      right: recipeTextDiff(baseSections[index].text, latestSections[index].text),
    }));
  });
  const visible = $derived(showUnchanged ? comparison : comparison.filter(field => field.left.length || field.right.length));
  const leftChanged = $derived(comparison.some(field => field.left.length));
  const rightChanged = $derived(comparison.some(field => field.right.length));

  onMount(async () => { await tick(); heading.focus(); });
</script>

<section class="conflict-review" aria-labelledby="recipe-conflict-heading">
  <h1 id="recipe-conflict-heading" tabindex="-1" bind:this={heading}>Review recipe changes</h1>
  {#if status === 'loading'}
    <Spinner label="Loading the latest recipe…" />
    {#if error}<p class="notice" role="status">{error}</p>{/if}
  {:else if status === 'error'}
    <p class="notice error" role="alert">{error}</p>
    <Button variant="secondary" onclick={onretry} disabled={busy}>Retry comparison</Button>
  {:else if latest}
    {#if base}
      <p>Your draft started from revision {base.revision}. Latest saved recipe: revision {latest.revision}.</p>
    {:else}
      <p class="notice">This older draft does not include its starting recipe. Showing differences between your draft and the latest saved recipe; these are not attributed changes.</p>
    {/if}
    {#if error}<p class="notice error" role="alert">{error}</p>{/if}
    <label class="unchanged-toggle"><input type="checkbox" bind:checked={showUnchanged}> Show unchanged fields</label>

    {#if base}
      <div class="column-labels" aria-hidden="true"><strong>Your changes · original → draft</strong><strong>Saved changes · original → revision {latest.revision}</strong></div>
      {#if !leftChanged}<p class="side-summary">Your draft: No editable content changes</p>{/if}
      {#if !rightChanged}<p class="side-summary">Latest saved recipe: No editable content changes</p>{/if}
    {:else}
      <h2 class="direct-label">Latest saved recipe → your draft</h2>
      {#if !leftChanged}<p class="side-summary">No editable content changes</p>{/if}
    {/if}

    <div class:direct={!base} class="fields">
      {#each visible as field (field.key)}
        <section class="field">
          <h2>{field.label}</h2>
          <div class="panels">
            <div class="panel" aria-label={base ? `Your changes to ${field.label}` : `${field.label}: latest saved recipe to your draft`}>
              {#if field.left.length}
                {#each field.left as hunk}
                  <pre class="range">{hunk.range}</pre>
                  {#each hunk.lines as line}<pre class:added={line.kind === 'addition'} class:removed={line.kind === 'removal'} class:marker={line.kind === 'marker'}>{line.text}</pre>{/each}
                {/each}
              {:else}<p class="no-change">No changes in this field</p>{/if}
            </div>
            {#if base}
              <div class="panel" aria-label={`Saved changes to ${field.label}`}>
                {#if field.right.length}
                  {#each field.right as hunk}
                    <pre class="range">{hunk.range}</pre>
                    {#each hunk.lines as line}<pre class:added={line.kind === 'addition'} class:removed={line.kind === 'removal'} class:marker={line.kind === 'marker'}>{line.text}</pre>{/each}
                  {/each}
                {:else}<p class="no-change">No changes in this field</p>{/if}
              </div>
            {/if}
          </div>
        </section>
      {/each}
    </div>
  {/if}

  {#if confirmation === 'replace'}
    <section class="notice confirmation" aria-labelledby="replace-heading">
      <h2 id="replace-heading">Replace the latest recipe with your draft?</h2>
      <p>Saved changes not present in your draft will be lost. Photos and authorship are not changed.</p>
      <div class="actions"><Button variant="danger" onclick={onreplace} disabled={busy}>Replace recipe</Button><Button variant="secondary" onclick={() => confirmation = null} disabled={busy}>Keep reviewing</Button></div>
    </section>
  {:else if confirmation === 'discard'}
    <section class="notice confirmation" aria-labelledby="discard-heading">
      <h2 id="discard-heading">Discard your draft from this device?</h2>
      <p>The saved recipe will not change.</p>
      <div class="actions"><Button variant="danger" onclick={ondiscard} disabled={busy}>Discard draft</Button><Button variant="secondary" onclick={() => confirmation = null} disabled={busy}>Keep reviewing</Button></div>
    </section>
  {:else}
    <div class="actions">
      <Button variant="primary" onclick={() => confirmation = 'replace'} disabled={busy || status !== 'ready' || !latest?.can_edit}>Save my version</Button>
      <Button variant="danger" onclick={() => confirmation = 'discard'} disabled={busy}>Discard my draft</Button>
      <Button variant="secondary" onclick={onback} disabled={busy}>Back to editing</Button>
    </div>
  {/if}
</section>

<style>
  .conflict-review{display:grid;gap:1rem}.conflict-review h1:focus{outline:2px solid var(--ui-accent);outline-offset:.25rem}.unchanged-toggle{display:flex;align-items:center;gap:.5rem}.unchanged-toggle input{width:auto}.column-labels,.panels{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1rem}.column-labels{position:sticky;top:0;z-index:1;padding:.75rem;background:var(--ui-surface-muted);border:1px solid var(--line);border-radius:.5rem}.fields{display:grid;gap:1.25rem}.field{min-width:0}.field h2{margin:0 0 .45rem;font-size:1.05rem}.panel{min-width:0;padding:.65rem;border:1px solid var(--line);border-radius:.5rem;background:var(--paper);overflow:hidden}.panel pre{margin:0;padding:.05rem .4rem;white-space:pre-wrap;overflow-wrap:anywhere;font:0.85rem/1.45 ui-monospace,SFMono-Regular,Consolas,monospace}.panel .range{margin:.25rem 0;color:var(--muted);font-weight:700}.added{background:#e3f3e5;color:#174d24}.removed{background:#f9e3e0;color:#79281f}.marker{color:var(--muted);font-style:italic}.no-change,.side-summary{color:var(--muted)}.actions{display:flex;flex-wrap:wrap;gap:.75rem}.confirmation{max-width:44rem}.confirmation h2{margin-top:0}.direct .panels{grid-template-columns:minmax(0,1fr)}.direct-label{font-size:1.1rem}
  @media(max-width:48rem){.column-labels{display:none}.panels{grid-template-columns:1fr}.panel::before{display:block;margin-bottom:.45rem;font-weight:700;content:attr(aria-label)}.actions :global(.ui-button){width:100%}}
</style>
