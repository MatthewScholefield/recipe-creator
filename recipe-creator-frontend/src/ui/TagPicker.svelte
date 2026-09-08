<script lang="ts">
  import Icon from './Icon.svelte';

  interface Props {
    tags: string[];
    selected?: string[];
    label?: string;
    placeholder?: string;
    single?: boolean;
    allowCreate?: boolean;
    onchange?: (tags: string[]) => void;
  }
  let { tags, selected = $bindable([]), label = 'Tags', placeholder = 'Search tags', single = false, allowCreate = true, onchange }: Props = $props();
  let query = $state('');
  let expanded = $state(false);
  let input: HTMLInputElement;
  let activeIndex = $state(0);
  const listId = `tags-${Math.random().toString(36).slice(2)}`;
  let normalized = $derived(query.trim().toLocaleLowerCase());
  let matches = $derived(tags.filter((tag) => !selected.includes(tag) && tag.toLocaleLowerCase().includes(normalized)));
  let createValue = $derived(query.trim());
  let canCreate = $derived(allowCreate && createValue.length > 0 && !tags.some((tag) => tag.localeCompare(createValue, undefined, {sensitivity: 'accent'}) === 0) && !selected.some((tag) => tag.localeCompare(createValue, undefined, {sensitivity: 'accent'}) === 0));
  let optionCount = $derived(matches.length + (canCreate ? 1 : 0));
  function choose(tag: string) { selected = single ? [tag] : [...selected, tag]; onchange?.(selected); query = ''; activeIndex = 0; if (single) expanded = false; input?.focus(); }
  function remove(tag: string) { selected = selected.filter((current) => current !== tag); onchange?.(selected); input?.focus(); }
  function keydown(event: KeyboardEvent) {
    if (event.key === 'ArrowDown') { event.preventDefault(); expanded = true; activeIndex = Math.min(activeIndex + 1, Math.max(optionCount - 1, 0)); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); expanded = true; activeIndex = Math.max(activeIndex - 1, 0); }
    else if (event.key === 'Enter' && expanded && optionCount) { event.preventDefault(); choose(activeIndex < matches.length ? matches[activeIndex] : createValue); }
    else if (event.key === 'Escape') expanded = false;
    else if (event.key === 'Backspace' && !query && selected.length) remove(selected.at(-1)!);
  }
</script>
<div class="tag-picker">
  <label for={listId}>{label}</label>
  <div class="tag-control" class:expanded onfocusout={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) expanded = false; }}>
    <div class="tag-chips">
      {#each selected as tag}<span class="tag-chip">{tag}<button type="button" aria-label={`Remove ${tag}`} onclick={() => remove(tag)}><Icon name="x" size={14} /></button></span>{/each}
      <input bind:this={input} id={listId} type="search" role="combobox" aria-expanded={expanded} aria-controls={`${listId}-options`} aria-activedescendant={expanded && optionCount ? `${listId}-option-${activeIndex}` : undefined} autocomplete="off" {placeholder} bind:value={query} onfocus={() => expanded = true} oninput={() => { expanded = true; activeIndex = 0; }} onkeydown={keydown} />
    </div>
    {#if expanded && (matches.length || canCreate)}
      <ul id={`${listId}-options`} role="listbox" aria-label={`${label} options`}>
        {#each matches as tag, index}<li id={`${listId}-option-${index}`} role="option" aria-selected={activeIndex === index}><button type="button" onclick={() => choose(tag)}><Icon name="check" size={15} />{tag}</button></li>{/each}
        {#if canCreate}<li id={`${listId}-option-${matches.length}`} role="option" aria-selected={activeIndex === matches.length}><button type="button" onclick={() => choose(createValue)}><Icon name="plus" size={15} />Create “{createValue}”</button></li>{/if}
      </ul>
    {/if}
  </div>
</div>
<style>
  .tag-picker{display:grid;gap:.3rem}.tag-control{position:relative;border:1px solid var(--ui-control-border);border-radius:.5rem;background:var(--ui-surface)}.tag-control.expanded,.tag-control:focus-within{border-color:var(--ui-accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--ui-accent) 20%,transparent)}.tag-chips{display:flex;flex-wrap:wrap;align-items:center;gap:.3rem;padding:.35rem}.tag-chips input{min-width:9rem;flex:1;border:0;outline:0;background:transparent;padding:.2rem;color:var(--ui-text)}.tag-chip{display:inline-flex;align-items:center;gap:.15rem;padding:.18rem .25rem .18rem .45rem;border-radius:999px;background:var(--ui-tag);color:var(--ui-text);font-size:.88rem}.tag-chip button{display:inline-flex;min-height:22px;min-width:22px;padding:0;border:0;border-radius:50%;background:transparent;color:inherit}.tag-chip button:hover{background:#0001}.tag-control ul{position:absolute;z-index:30;top:calc(100% + .25rem);left:0;right:0;max-height:15rem;overflow:auto;margin:0;padding:.3rem;list-style:none;border:1px solid var(--ui-control-border);border-radius:.5rem;background:var(--ui-surface);box-shadow:0 .5rem 1.5rem #1112}.tag-control li button{display:flex;width:100%;min-height:34px;align-items:center;gap:.4rem;padding:.35rem .45rem;border:0;border-radius:.3rem;background:transparent;color:var(--ui-text);text-align:left}.tag-control li[aria-selected=true] button,.tag-control li button:hover{background:var(--ui-surface-muted)}
</style>
