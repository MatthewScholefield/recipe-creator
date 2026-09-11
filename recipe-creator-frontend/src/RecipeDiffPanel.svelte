<script lang="ts">
  import type { RecipeDiffHunk } from './recipe-diff';

  let {hunks, ariaLabel, emptyText = 'No changes in this field', compact = false, mobileLabel = false}: {
    hunks: RecipeDiffHunk[];
    ariaLabel: string;
    emptyText?: string;
    compact?: boolean;
    mobileLabel?: boolean;
  } = $props();
</script>

<div class:compact class:mobile-label={mobileLabel} class="panel" aria-label={ariaLabel}>
  {#if hunks.length}
    {#each hunks as hunk}
      <pre class="range">{hunk.range}</pre>
      {#each hunk.lines as line}<pre class:added={line.kind === 'addition'} class:removed={line.kind === 'removal'} class:marker={line.kind === 'marker'}>{line.text}</pre>{/each}
    {/each}
  {:else}<p class="no-change">{emptyText}</p>{/if}
</div>

<style>
  .panel{min-width:0;padding:.65rem;border:1px solid var(--line);border-radius:.5rem;background:var(--paper);overflow:hidden}.panel pre{margin:0;padding:.05rem .4rem;white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.84rem}.range,.marker{color:var(--ui-text-muted)}.added{background:#e7f6ec;color:#165b2d}.removed{background:#fdebec;color:#8a1c25}.no-change{margin:.25rem;color:var(--ui-text-muted)}.compact{max-height:11rem;overflow:auto}
  @media(max-width:48rem){.mobile-label::before{display:block;margin-bottom:.45rem;font-weight:700;content:attr(aria-label)}}
</style>
