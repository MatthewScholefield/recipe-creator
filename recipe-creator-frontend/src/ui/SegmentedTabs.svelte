<script lang="ts">
  export interface TabOption {
    value: string;
    label: string;
  }

  interface Props {
    options: TabOption[];
    value: string;
    ariaLabel: string;
    onchange: (value: string) => void;
  }

  let { options, value, ariaLabel, onchange }: Props = $props();
</script>

<div class="segmented-tabs" role="tablist" aria-label={ariaLabel}>
  {#each options as option}
    <button
      type="button"
      class="tab"
      class:active={option.value === value}
      role="tab"
      aria-selected={option.value === value}
      onclick={() => onchange(option.value)}
    >{option.label}</button>
  {/each}
</div>

<style>
  .segmented-tabs{display:inline-flex;gap:.25rem;padding:.25rem;border:1px solid var(--ui-control-border);border-radius:.75rem;background:var(--ui-surface)}
  .tab{min-height:32px;padding:.375rem .75rem;border:0;border-radius:.5rem;background:transparent;color:var(--muted);font:inherit;font-size:.875rem;font-weight:500;cursor:pointer;transition:background 150ms,color 150ms}
  .tab:hover,.tab.active{background:var(--ui-surface-muted);color:var(--ui-text)}
  .tab:focus-visible{outline:2px solid var(--ui-accent);outline-offset:2px}
</style>
