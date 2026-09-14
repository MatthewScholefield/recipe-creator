<script lang="ts">
  import { onMount, type Snippet } from 'svelte';
  import { keepInViewportHorizontally } from './overlay';
  interface TriggerState { id: string; expanded: boolean; toggle: () => void }
  interface Props { text: string; children?: Snippet; clickable?: boolean; trigger?: Snippet<[TriggerState]>; }
  let { text, children, clickable = false, trigger }: Props = $props();
  let wrapper: HTMLSpanElement;
  let hovered = $state(false), focused = $state(false), pinned = $state(false), dismissed = $state(false);
  const visible = $derived(pinned || (!dismissed && (hovered || focused)));
  const id = `tooltip-${Math.random().toString(36).slice(2)}`;
  function maybeResetDismissed() { if (!hovered && !focused) dismissed = false; }
  function toggle() {
    if (!clickable) return;
    if (pinned) { pinned = false; dismissed = true; }
    else { pinned = true; dismissed = false; }
  }
  function dismiss() { pinned = false; dismissed = true; }
  onMount(() => {
    const pointerdown = (event: PointerEvent) => {
      if (clickable && visible && !wrapper.contains(event.target as Node)) dismiss();
    };
    const keydown = (event: KeyboardEvent) => {
      if (clickable && event.key === 'Escape' && visible) dismiss();
    };
    document.addEventListener('pointerdown', pointerdown);
    document.addEventListener('keydown', keydown);
    return () => {
      document.removeEventListener('pointerdown', pointerdown);
      document.removeEventListener('keydown', keydown);
    };
  });
</script>
<span bind:this={wrapper} class="tooltip-wrap" role="presentation"
  onmouseenter={() => { hovered = true; }}
  onmouseleave={() => { hovered = false; maybeResetDismissed(); }}
  onfocusin={() => { focused = true; }}
  onfocusout={(event) => {
    if (!wrapper.contains(event.relatedTarget as Node | null)) {
      focused = false;
      pinned = false;
      maybeResetDismissed();
    }
  }}
  aria-describedby={!trigger && visible ? id : undefined} tabindex="-1">
  {#if trigger}
    {@render trigger({id, expanded: visible, toggle})}
  {:else if children}
    {@render children()}
  {/if}
  {#if visible}<span class="tooltip" use:keepInViewportHorizontally id={id} role="tooltip">{text}</span>{/if}
</span>
<style>
  .tooltip-wrap{position:relative;display:inline-flex}.tooltip{position:absolute;z-index:20;bottom:calc(100% + .45rem);left:50%;box-sizing:border-box;width:max-content;max-width:min(16rem,calc(100vw - 1.5rem));transform:translateX(calc(-50% + var(--overlay-shift-x, 0px)));padding:.35rem .55rem;border-radius:.35rem;background:var(--ui-tooltip);color:#fff;font-size:.82rem;line-height:1.35;white-space:pre-line;box-shadow:0 3px 10px #0003}
</style>
