<script lang="ts">
  import type { Snippet } from 'svelte';
  import Button from './Button.svelte';

  interface Props {
    children: Snippet;
    href: string;
    ariaLabel: string;
    title?: string;
  }

  let { children, href, ariaLabel, title }: Props = $props();
</script>

<Button variant="ghost" size="sm" class="icon-only" {href} {ariaLabel} {title}>
  {@render children()}
</Button>
