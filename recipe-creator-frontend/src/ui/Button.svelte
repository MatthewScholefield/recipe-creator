<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    children: Snippet;
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
    size?: 'sm' | 'md';
    type?: 'button' | 'submit' | 'reset';
    disabled?: boolean;
    href?: string;
    ariaLabel?: string;
    onclick?: (event: MouseEvent) => void;
    class?: string;
  }
  let { children, variant = 'secondary', size = 'md', type = 'button', disabled = false, href, ariaLabel, onclick, class: className = '' }: Props = $props();
</script>
{#if href}
  <a class={`ui-button ${variant} ${size} ${className}`} {href} aria-label={ariaLabel} onclick={onclick}>{@render children()}</a>
{:else}
  <button class={`ui-button ${variant} ${size} ${className}`} {type} {disabled} aria-label={ariaLabel} {onclick}>{@render children()}</button>
{/if}
<style>
  .ui-button{gap:.45rem;border:1px solid var(--ui-control-border);border-radius:.5rem;font-weight:650;text-decoration:none;transition:background-color .15s ease,border-color .15s ease,color .15s ease,box-shadow .15s ease}.ui-button.secondary{background:var(--ui-surface);color:var(--ui-text)}.ui-button.secondary:hover{background:var(--ui-surface-muted)}.ui-button.primary{background:var(--ui-accent);border-color:var(--ui-accent);color:#fff}.ui-button.primary:hover{background:var(--ui-accent-strong);border-color:var(--ui-accent-strong)}.ui-button.ghost{background:transparent;border-color:transparent;color:var(--ui-text)}.ui-button.ghost:hover{background:var(--ui-surface-muted)}.ui-button.danger{background:var(--ui-danger);border-color:var(--ui-danger);color:#fff}.ui-button.danger:hover{background:#982d23}.ui-button.sm{min-height:34px;padding:.3rem .55rem;font-size:.9rem}.ui-button:disabled{opacity:.55;cursor:not-allowed}
</style>
