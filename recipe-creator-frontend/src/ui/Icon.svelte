<script lang="ts">
  import Search from '@lucide/svelte/icons/search';
  import X from '@lucide/svelte/icons/x';
  import ChevronDown from '@lucide/svelte/icons/chevron-down';
  import Check from '@lucide/svelte/icons/check';
  import Plus from '@lucide/svelte/icons/plus';
  import LoaderCircle from '@lucide/svelte/icons/loader-circle';
  import User from '@lucide/svelte/icons/user';
  import Trash2 from '@lucide/svelte/icons/trash-2';
  import Pencil from '@lucide/svelte/icons/pencil';
  import Bookmark from '@lucide/svelte/icons/bookmark';
  import Share2 from '@lucide/svelte/icons/share-2';
  import type { Component } from 'svelte';

  export type IconName = 'search' | 'x' | 'chevron-down' | 'check' | 'plus' | 'loader' | 'user' | 'trash' | 'edit' | 'bookmark' | 'share';
  interface Props { name: IconName; size?: number; strokeWidth?: number; label?: string; }
  let { name, size = 20, strokeWidth = 2, label }: Props = $props();
  const icons: Record<IconName, Component> = {search: Search, x: X, 'chevron-down': ChevronDown, check: Check, plus: Plus, loader: LoaderCircle, user: User, trash: Trash2, edit: Pencil, bookmark: Bookmark, share: Share2};
  let selected = $derived(icons[name]);
</script>
<selected {size} {strokeWidth} aria-label={label} aria-hidden={label ? undefined : 'true'}></selected>
