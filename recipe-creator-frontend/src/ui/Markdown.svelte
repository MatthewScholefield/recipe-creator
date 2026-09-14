<script lang="ts">
  type InlineNode =
    | { type: 'text'; value: string }
    | { type: 'strong' | 'emphasis'; children: InlineNode[] }
    | { type: 'link'; href: string; children: InlineNode[] };

  type BlockNode =
    | { type: 'paragraph'; children: InlineNode[] }
    | { type: 'heading'; level: number; children: InlineNode[] }
    | { type: 'list'; ordered: boolean; items: InlineNode[][] };

  interface Props {
    source: string;
    class?: string;
  }

  let { source, class: className = '' }: Props = $props();

  function appendText(nodes: InlineNode[], value: string) {
    if (!value) return;
    const previous = nodes.at(-1);
    if (previous?.type === 'text') previous.value += value;
    else nodes.push({ type: 'text', value });
  }

  function safeHttpUrl(value: string): string | undefined {
    if (value !== value.trim() || /[\u0000-\u001f\u007f]/.test(value)) return;
    try {
      const url = new URL(value);
      return url.protocol === 'http:' || url.protocol === 'https:' ? url.href : undefined;
    } catch {
      return;
    }
  }

  function delimiterCanOpen(text: string, index: number): boolean {
    return index === 0 || /[\s([{>\-]/.test(text[index - 1]);
  }

  function delimiterCanClose(text: string, index: number): boolean {
    return index === text.length || /[\s.,!?;:)}\]>\-]/.test(text[index]);
  }

  function parseInline(text: string): InlineNode[] {
    const nodes: InlineNode[] = [];
    let plainStart = 0;
    let index = 0;

    const flushPlain = () => {
      appendText(nodes, text.slice(plainStart, index));
    };

    while (index < text.length) {
      if (text[index] === '[') {
        const labelEnd = text.indexOf('](', index + 1);
        const targetEnd = labelEnd < 0 ? -1 : text.indexOf(')', labelEnd + 2);
        if (labelEnd > index + 1 && targetEnd > labelEnd + 2) {
          const href = safeHttpUrl(text.slice(labelEnd + 2, targetEnd));
          if (href) {
            flushPlain();
            nodes.push({ type: 'link', href, children: parseInline(text.slice(index + 1, labelEnd)) });
            index = targetEnd + 1;
            plainStart = index;
            continue;
          }
        }
      }

      const marker = text.startsWith('**', index)
        ? '**'
        : text.startsWith('__', index)
          ? '__'
          : text[index] === '*'
            ? '*'
            : text[index] === '_'
              ? '_'
              : undefined;
      if (marker && delimiterCanOpen(text, index)) {
        const contentStart = index + marker.length;
        let markerEnd = text.indexOf(marker, contentStart);
        while (markerEnd > contentStart && !delimiterCanClose(text, markerEnd + marker.length)) {
          markerEnd = text.indexOf(marker, markerEnd + marker.length);
        }
        if (markerEnd > contentStart) {
          flushPlain();
          nodes.push({
            type: marker.length === 2 ? 'strong' : 'emphasis',
            children: parseInline(text.slice(contentStart, markerEnd))
          });
          index = markerEnd + marker.length;
          plainStart = index;
          continue;
        }
      }
      index += 1;
    }
    flushPlain();
    return nodes;
  }

  function heading(line: string) {
    return /^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$/.exec(line);
  }

  function listItem(line: string): { ordered: boolean; text: string } | undefined {
    const unordered = /^\s*[-+*]\s+(.+)$/.exec(line);
    if (unordered) return { ordered: false, text: unordered[1] };
    const ordered = /^\s*\d+[a-z]?\.\s+(.+)$/i.exec(line);
    if (ordered) return { ordered: true, text: ordered[1] };
  }

  function parseMarkdown(value: string): BlockNode[] {
    const lines = value.replace(/\r\n?/g, '\n').split('\n');
    const blocks: BlockNode[] = [];
    let index = 0;

    while (index < lines.length) {
      if (!lines[index].trim()) {
        index += 1;
        continue;
      }

      const headingMatch = heading(lines[index]);
      if (headingMatch) {
        blocks.push({
          type: 'heading',
          level: headingMatch[1].length,
          children: parseInline(headingMatch[2])
        });
        index += 1;
        continue;
      }

      const firstItem = listItem(lines[index]);
      if (firstItem) {
        const items: InlineNode[][] = [];
        while (index < lines.length) {
          const item = listItem(lines[index]);
          if (!item || item.ordered !== firstItem.ordered) break;
          items.push(parseInline(item.text));
          index += 1;

          let nextIndex = index;
          while (nextIndex < lines.length && !lines[nextIndex].trim()) nextIndex += 1;
          const nextItem = listItem(lines[nextIndex] ?? '');
          if (nextItem?.ordered === firstItem.ordered) index = nextIndex;
        }
        blocks.push({ type: 'list', ordered: firstItem.ordered, items });
        continue;
      }

      const paragraphLines: string[] = [];
      while (
        index < lines.length &&
        lines[index].trim() &&
        !heading(lines[index]) &&
        !listItem(lines[index])
      ) {
        paragraphLines.push(lines[index].trim());
        index += 1;
      }
      blocks.push({ type: 'paragraph', children: parseInline(paragraphLines.join(' ')) });
    }
    return blocks;
  }

  let blocks = $derived(parseMarkdown(source));
</script>

{#snippet inline(nodes: InlineNode[])}
  {#each nodes as node}
    {#if node.type === 'text'}
      {node.value}
    {:else if node.type === 'strong'}
      <strong>{@render inline(node.children)}</strong>
    {:else if node.type === 'emphasis'}
      <em>{@render inline(node.children)}</em>
    {:else if node.type === 'link'}
      <a href={node.href}>{@render inline(node.children)}</a>
    {/if}
  {/each}
{/snippet}

<div class={`markdown${className ? ` ${className}` : ''}`}>
  {#each blocks as block}
    {#if block.type === 'paragraph'}
      <p>{@render inline(block.children)}</p>
    {:else if block.type === 'heading'}
      {#if block.level === 1}<h1>{@render inline(block.children)}</h1>
      {:else if block.level === 2}<h2>{@render inline(block.children)}</h2>
      {:else if block.level === 3}<h3>{@render inline(block.children)}</h3>
      {:else if block.level === 4}<h4>{@render inline(block.children)}</h4>
      {:else if block.level === 5}<h5>{@render inline(block.children)}</h5>
      {:else}<h6>{@render inline(block.children)}</h6>{/if}
    {:else if block.ordered}
      <ol>{#each block.items as item}<li>{@render inline(item)}</li>{/each}</ol>
    {:else}
      <ul>{#each block.items as item}<li>{@render inline(item)}</li>{/each}</ul>
    {/if}
  {/each}
</div>

<style>
  .markdown{color:inherit;font:inherit;line-height:inherit;overflow-wrap:anywhere}
  .markdown p,.markdown ul,.markdown ol{margin:.65em 0}
  .markdown p:first-child,.markdown ul:first-child,.markdown ol:first-child,.markdown :is(h1,h2,h3,h4,h5,h6):first-child{margin-top:0}
  .markdown p:last-child,.markdown ul:last-child,.markdown ol:last-child,.markdown :is(h1,h2,h3,h4,h5,h6):last-child{margin-bottom:0}
  .markdown ul,.markdown ol{padding-inline-start:1.5em}
  .markdown li{margin:.2em 0}
  .markdown :is(h1,h2,h3,h4,h5,h6){margin:1em 0 .4em;color:inherit;font-family:inherit;line-height:1.25}
  .markdown h1{font-size:1.35em}.markdown h2{font-size:1.25em}.markdown h3{font-size:1.15em}.markdown h4,.markdown h5,.markdown h6{font-size:1em}
  .markdown a{color:var(--ui-accent);text-decoration:underline;text-underline-offset:.15em}
  .markdown a:focus-visible{outline:2px solid var(--ui-accent);outline-offset:2px}
</style>
