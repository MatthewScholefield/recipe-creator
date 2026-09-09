<script lang="ts">
  import Icon from './ui/Icon.svelte';

  interface Props {
    title: string;
    href: string;
  }

  let { title, href }: Props = $props();
</script>

<a class="recipe-row" {href}>
  <span class="recipe-title">{title}</span>
  <span class="recipe-row-action">View recipe <Icon name="arrow-right" size={16} /></span>
</a>

<style>
  .recipe-row{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.8rem 1rem;border:1px solid var(--line);border-radius:.55rem;background:var(--paper);color:var(--ui-text);text-decoration:none;transition:background-color .15s ease,border-color .15s ease}.recipe-row:hover,.recipe-row:focus-visible{background:var(--ui-surface-muted);border-color:var(--ui-control-border);color:var(--ui-text)}.recipe-title{font-family:Georgia,serif;font-size:1.1rem}.recipe-row-action{display:inline-flex;align-items:center;gap:.35rem;color:var(--ui-accent);font-size:.9rem;font-weight:650;white-space:nowrap}.recipe-row :global(svg){flex-shrink:0}@media(max-width:500px){.recipe-row{align-items:flex-start;flex-direction:column;gap:.35rem}}
</style>
