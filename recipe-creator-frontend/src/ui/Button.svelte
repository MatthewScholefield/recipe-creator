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
    title?: string;
    target?: string;
    rel?: string;
    onclick?: (event: MouseEvent) => void;
    class?: string;
  }
  let { children, variant = 'secondary', size = 'md', type = 'button', disabled = false, href, ariaLabel, title, target, rel, onclick, class: className = '' }: Props = $props();
</script>
{#if href}
  <a class={`ui-button ${variant} ${size} ${className}`} {href} {title} {target} {rel} aria-label={ariaLabel} onclick={onclick}>{@render children()}</a>
{:else}
  <button class={`ui-button ${variant} ${size} ${className}`} {type} {disabled} {title} aria-label={ariaLabel} {onclick}>{@render children()}</button>
{/if}
<style>
  .ui-button{display:inline-flex;align-items:center;justify-content:center;min-height:44px;padding:.55rem 1rem;gap:.45rem;border:1px solid var(--ui-control-border);border-radius:.5rem;font-weight:650;cursor:pointer;text-decoration:none;transition:background-color .15s ease,border-color .15s ease,color .15s ease,box-shadow .15s ease}.ui-button.secondary{background:var(--ui-surface);color:var(--ui-text)}.ui-button.secondary:hover{background:var(--ui-surface-muted)}.ui-button.primary{background:var(--ui-accent);border-color:var(--ui-accent);color:#fff}.ui-button.primary:hover{background:var(--ui-accent-strong);border-color:var(--ui-accent-strong)}.ui-button.ghost{background:transparent;border-color:transparent;color:var(--ui-text)}.ui-button.ghost:hover{background:var(--ui-surface-muted)}.ui-button.danger{background:var(--ui-danger);border-color:var(--ui-danger);color:#fff}.ui-button.sm{min-height:36px;padding:.4rem .7rem;font-size:.9rem}.ui-button.icon-only{width:36px;min-width:36px;padding:.4rem}.ui-button:focus-visible{outline:3px solid #ad6b34;outline-offset:3px}
</style>
