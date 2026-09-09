<script lang="ts">
  import Icon from './Icon.svelte';

  interface Props {
    label: string;
    href?: string;
    removable?: boolean;
    onclick?: () => void;
    iconOnly?: boolean;
    expanded?: boolean;
  }
  let { label, href, removable = false, onclick, iconOnly = false, expanded }: Props = $props();
  let accessibleLabel = $derived(removable ? `Remove ${label}` : iconOnly ? label : undefined);
</script>

{#snippet content()}
  {#if iconOnly}<Icon name="search" size={15} />{:else}<span class="tag-text">{label}</span>{/if}
{/snippet}

{#if removable}
  <span class="tag">
    <span class="tag-text">{label}</span>
    {#if href !== undefined}<a class="remove" {href} {onclick} aria-label={accessibleLabel}><Icon name="x" size={14} /></a>
    {:else}<button class="remove" type="button" {onclick} aria-label={accessibleLabel}><Icon name="x" size={14} /></button>{/if}
  </span>
{:else if href !== undefined}
  <a class="tag" class:icon-only={iconOnly} {href} {onclick} aria-label={accessibleLabel} aria-expanded={expanded}>{@render content()}</a>
{:else if onclick}
  <button class="tag" class:icon-only={iconOnly} type="button" {onclick} aria-label={accessibleLabel} aria-expanded={expanded}>{@render content()}</button>
{:else}
  <span class="tag" class:icon-only={iconOnly} aria-label={accessibleLabel}>{@render content()}</span>
{/if}

<style>
  .tag{display:inline-flex;align-items:center;justify-content:center;gap:.3rem;padding:.25rem .7rem;border:0;border-radius:999px;background:var(--ui-tag);color:var(--ui-text);font-family:inherit;font-size:.88rem;font-weight:400;line-height:1.3;text-decoration:none;vertical-align:middle;margin:0;width:auto;min-height:0}
  .tag:hover,.tag:focus{background:var(--ui-tag);text-decoration:none}
  button.tag,a.tag{cursor:pointer}
  .tag:focus-visible{outline:2px solid var(--ui-accent);outline-offset:2px}
  .tag-text{position:relative;top:-1px}
  .tag.icon-only{padding:.35rem .7rem}
  .remove{display:inline-flex;align-items:center;justify-content:center;padding:0;margin:0;min-height:0;border:0;background:transparent;color:inherit;text-decoration:none;cursor:pointer;line-height:1}
  .remove:hover{background:transparent;color:var(--ui-accent)}
  .remove :global(svg){margin:-1.5px}
</style>
