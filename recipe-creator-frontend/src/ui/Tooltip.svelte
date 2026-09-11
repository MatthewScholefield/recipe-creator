<script lang="ts">
  import type { Snippet } from 'svelte';
  import { keepInViewportHorizontally } from './overlay';
  interface Props { text: string; children: Snippet; }
  let { text, children }: Props = $props();
  let visible = $state(false);
  const id = `tooltip-${Math.random().toString(36).slice(2)}`;
</script>
<span class="tooltip-wrap" role="presentation" onmouseenter={() => visible = true} onmouseleave={() => visible = false} onfocusin={() => visible = true} onfocusout={() => visible = false} aria-describedby={visible ? id : undefined} tabindex="-1">
  {@render children()}
  {#if visible}<span class="tooltip" use:keepInViewportHorizontally id={id} role="tooltip">{text}</span>{/if}
</span>
<style>
  .tooltip-wrap{position:relative;display:inline-flex}.tooltip{position:absolute;z-index:20;bottom:calc(100% + .45rem);left:50%;box-sizing:border-box;width:max-content;max-width:min(16rem,calc(100vw - 1.5rem));transform:translateX(calc(-50% + var(--overlay-shift-x, 0px)));padding:.35rem .55rem;border-radius:.35rem;background:var(--ui-tooltip);color:#fff;font-size:.82rem;line-height:1.35;white-space:pre-line;box-shadow:0 3px 10px #0003}
</style>
