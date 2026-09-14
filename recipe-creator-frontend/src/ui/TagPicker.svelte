<script lang="ts">
  import { tick } from 'svelte';
  import Icon from './Icon.svelte';
  import { canonicalTag, tagInput } from '../recipe';
  import Tag from './Tag.svelte';

  interface Props {
    tags: string[];
    selected?: string[];
    label?: string;
    placeholder?: string;
    single?: boolean;
    compact?: boolean;
    allowCreate?: boolean;
    onchange?: (tags: string[]) => void;
  }
  let { tags, selected = $bindable([]), label = 'Tags', placeholder = 'Search tags', single = false, compact = false, allowCreate = true, onchange }: Props = $props();
  let query = $state('');
  let expanded = $state(false);
  let input = $state<HTMLInputElement>();
  let picker = $state<HTMLDivElement>();
  async function open() { expanded = true; await tick(); input?.focus(); }
  async function collapse(restoreFocus = false) {
    expanded = false;
    if (compact) query = '';
    if (compact && restoreFocus) { await tick(); picker?.querySelector('button')?.focus(); }
  }
  let activeIndex = $state(0);
  const listId = `tags-${Math.random().toString(36).slice(2)}`;
  let normalized = $derived(query.trim().toLocaleLowerCase());
  let matches = $derived(tags.filter((tag) => !selected.includes(tag) && tag.toLocaleLowerCase().includes(normalized)));
  let createValue = $derived(canonicalTag(query));
  let canCreate = $derived(allowCreate && query === createValue && createValue.length > 0 && [...createValue].length <= 80 && !tags.some((tag) => tag === createValue) && !selected.some((tag) => tag === createValue));
  let optionCount = $derived(matches.length + (canCreate ? 1 : 0));
  function choose(tag: string) { const value = canonicalTag(tag); if (!value) return; selected = single ? [value] : [...selected, value]; onchange?.(selected); query = ''; activeIndex = 0; if (single) expanded = false; input?.focus(); }
  function remove(tag: string) { selected = selected.filter((current) => current !== tag); onchange?.(selected); input?.focus(); }
  function keydown(event: KeyboardEvent) {
    if (event.key === 'ArrowDown') { event.preventDefault(); expanded = true; activeIndex = Math.min(activeIndex + 1, Math.max(optionCount - 1, 0)); }
    else if (event.key === 'ArrowUp') { event.preventDefault(); expanded = true; activeIndex = Math.max(activeIndex - 1, 0); }
    else if (event.key === 'Enter' && expanded && optionCount) { event.preventDefault(); choose(activeIndex < matches.length ? matches[activeIndex] : createValue); }
    else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); void collapse(true); }
    else if (event.key === 'Backspace' && !query && selected.length) remove(selected.at(-1)!);
  }
</script>
<div class="tag-picker" class:compact bind:this={picker}>
  {#if compact && !expanded}
    <Tag label="Search tags" iconOnly expanded={false} onclick={open} />
  {:else}
  {#if !compact}<label for={listId}>{label}</label>{/if}
  <div class="tag-control" class:expanded onfocusout={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) void collapse(); }}>
    <div class="tag-chips">
      {#if !compact}{#each selected as tag}<Tag label={tag} removable onclick={() => remove(tag)} />{/each}{/if}
      <input bind:this={input} id={listId} aria-label={compact ? label : undefined} type="search" role="combobox" aria-expanded={expanded} aria-controls={`${listId}-options`} aria-activedescendant={expanded && optionCount ? `${listId}-option-${activeIndex}` : undefined} autocomplete="off" {placeholder} maxlength="80" value={query} onfocus={() => expanded = true} oninput={(event) => { query = tagInput(event.currentTarget.value); expanded = true; activeIndex = 0; }} onkeydown={keydown} />
    </div>
    {#if expanded && (matches.length || canCreate)}
      <ul id={`${listId}-options`} role="listbox" aria-label={`${label} options`}>
        {#each matches as tag, index}<li id={`${listId}-option-${index}`} role="option" aria-selected={activeIndex === index}><button type="button" onclick={() => choose(tag)}>{tag}</button></li>{/each}
        {#if canCreate}<li id={`${listId}-option-${matches.length}`} role="option" aria-selected={activeIndex === matches.length}><button type="button" onclick={() => choose(createValue)}><Icon name="plus" size={15} />Create “{createValue}”</button></li>{/if}
      </ul>
    {/if}
  </div>
  {/if}
</div>
<style>
  .tag-picker{display:grid;gap:.3rem}
  .tag-picker.compact{display:inline-grid;vertical-align:middle}
  .tag-control{position:relative;border:1px solid var(--ui-control-border);border-radius:.5rem;background:var(--ui-surface)}
  .tag-control.expanded,.tag-control:focus-within{border-color:var(--ui-accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--ui-accent) 20%,transparent)}
  .tag-chips{display:flex;flex-wrap:wrap;align-items:center;gap:.3rem;padding:.5rem .6rem}
  .tag-chips input{min-width:0;width:9rem;flex:1 1 9rem;margin:0;border:0;outline:0;background:transparent;padding:.2rem;color:var(--ui-text);box-shadow:none}
  .compact .tag-control{border-radius:999px}
  .compact .tag-chips{flex-wrap:nowrap;padding:0 .55rem}
  .compact .tag-chips input{width:7rem;flex:0 1 7rem;height:1.5rem;padding:0;font-size:.88rem;line-height:1.3}
  ul{position:absolute;top:calc(100% + .3rem);left:0;z-index:20;display:flex;flex-direction:column;gap:.2rem;width:max-content;min-width:100%;max-width:min(20rem,calc(100vw - 2rem));list-style:none;margin:0;padding:.2rem;max-height:15rem;overflow:auto;border:1px solid var(--ui-control-border);border-radius:16.5px;background:var(--ui-surface);box-shadow:0 6px 16px #0002}
  li{margin:0;padding:0}
  li button{display:flex;align-items:center;gap:.3rem;width:100%;min-height:1.65rem;margin:0;padding:.25rem .5rem;border:0;border-radius:16.5px;background:transparent;color:var(--ui-text);text-align:left;font:inherit;font-size:.88rem;line-height:1.3;cursor:pointer}
  li[aria-selected='true'] button,li button:hover{background:var(--ui-tag)}
</style>
