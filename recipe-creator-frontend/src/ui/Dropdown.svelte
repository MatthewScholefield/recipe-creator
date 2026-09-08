<script lang="ts">
  import { tick, type Snippet } from 'svelte';
  import Icon from './Icon.svelte';
  interface Props { label: string; children: Snippet; trigger?: Snippet<[boolean]>; }
  let { label, children, trigger }: Props = $props();
  let open = $state(false);
  let root: HTMLDivElement;
  let triggerElement = $state<HTMLElement>();
  function close(focus = true) { open = false; if (focus) tick().then(() => triggerElement?.focus()); }
  function toggle() { open ? close(false) : open = true; }
  function setCustomTrigger(event: Event) {
    const target = event.target;
    const element = target instanceof Element
      ? target.closest<HTMLElement>('button, a[href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
      : null;
    if (element && root?.contains(element)) triggerElement = element;
  }
  $effect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => { if (!root?.contains(event.target as Node)) close(false); };
    const keydown = (event: KeyboardEvent) => { if (event.key === 'Escape') { event.preventDefault(); close(); } };
    document.addEventListener('pointerdown', outside); document.addEventListener('keydown', keydown);
    tick().then(() => root?.querySelector<HTMLElement>('[role="menu"] button, [role="menu"] a, [role="menu"] [tabindex]')?.focus());
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', keydown); };
  });
</script>
<div class="dropdown" bind:this={root}>
  {#if trigger}
    <span role="presentation" onclick={(event) => { setCustomTrigger(event); toggle(); }} onkeydown={(event) => { setCustomTrigger(event); if (event.key === 'ArrowDown') { event.preventDefault(); open = true; } }}>{@render trigger(open)}</span>
  {:else}
    <button bind:this={triggerElement} type="button" aria-haspopup="menu" aria-expanded={open} onclick={toggle} onkeydown={(event) => { if (event.key === 'ArrowDown') { event.preventDefault(); open = true; } }}>{label}<Icon name="chevron-down" size={16} /></button>
  {/if}
  {#if open}<div class="dropdown-menu" role="menu" tabindex="-1" aria-label={label} onclick={(event) => { if ((event.target as Element).closest('button,a,[role="menuitem"]')) close(false); }} onkeydown={() => {}}>{@render children()}</div>{/if}
</div>
<style>
  .dropdown{position:relative;display:inline-block}.dropdown>button{gap:.35rem}.dropdown-menu{position:absolute;z-index:30;right:0;top:calc(100% + .35rem);min-width:12rem;padding:.35rem;border:1px solid var(--ui-control-border);border-radius:.5rem;background:var(--ui-surface);box-shadow:0 .5rem 1.5rem #1112}.dropdown-menu :global(button),.dropdown-menu :global(a){display:flex;width:100%;min-height:34px;padding:.4rem .55rem;border:0;border-radius:.3rem;background:transparent;color:var(--ui-text);text-align:left;text-decoration:none}.dropdown-menu :global(button:hover),.dropdown-menu :global(a:hover){background:var(--ui-surface-muted)}
</style>
