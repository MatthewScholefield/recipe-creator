<script lang="ts">
  import { tick, type Snippet } from 'svelte';
  interface Props { open?: boolean; title: string; children: Snippet; onclose?: () => void; }
  let { open = $bindable(false), title, children, onclose }: Props = $props();
  let dialog = $state<HTMLDialogElement>();
  const titleId = `modal-${Math.random().toString(36).slice(2)}`;
  function close() { open = false; onclose?.(); }
  $effect(() => {
    if (!open) return;
    const focused = document.activeElement as HTMLElement | null;
    tick().then(() => { if (dialog && !dialog.open) { if (dialog.showModal) dialog.showModal(); else dialog.setAttribute('open', ''); } dialog?.querySelector<HTMLElement>('[autofocus], button, [href], input, select, textarea')?.focus(); });
    return () => { if (dialog?.open && typeof dialog.close === 'function') dialog.close(); focused?.focus(); }; 
  });
</script>
{#if open}
  <dialog bind:this={dialog} class="ui-modal" aria-labelledby={titleId} oncancel={(event) => { event.preventDefault(); close(); }} onclick={(event) => { if (event.target === dialog) close(); }}>
    <section><header><h2 id={titleId}>{title}</h2><button class="modal-close" type="button" aria-label="Close dialog" onclick={close}>×</button></header>{@render children()}</section>
  </dialog>
{/if}
<style>
  .ui-modal{width:min(32rem,calc(100% - 2rem));max-height:calc(100% - 2rem);padding:0;border:0;border-radius:.75rem;background:var(--ui-surface);color:var(--ui-text);box-shadow:0 1rem 3rem #1116}.ui-modal::backdrop{background:#19211980}.ui-modal section{padding:1.25rem}.ui-modal header{display:flex;align-items:start;justify-content:space-between;gap:1rem;margin-bottom:1rem}.ui-modal h2{margin:0;font-size:1.3rem}.modal-close{min-width:34px;min-height:34px;padding:0;border:0;border-radius:.35rem;background:transparent;color:var(--ui-text);font-size:1.5rem;line-height:1}.modal-close:hover{background:var(--ui-surface-muted)}
</style>
