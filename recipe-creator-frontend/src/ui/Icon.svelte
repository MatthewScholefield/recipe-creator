<script lang="ts">
  import Search from '@lucide/svelte/icons/search';
  import Sun from '@lucide/svelte/icons/sun';
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
  import ArrowLeft from '@lucide/svelte/icons/arrow-left';
  import ArrowRight from '@lucide/svelte/icons/arrow-right';
  import ExternalLink from '@lucide/svelte/icons/external-link';
  import ShieldCheck from '@lucide/svelte/icons/shield-check';
  import GitCompare from '@lucide/svelte/icons/git-compare';
  import type { Component } from 'svelte';

  export type IconName = 'search' | 'x' | 'chevron-down' | 'check' | 'plus' | 'loader' | 'user' | 'trash' | 'edit' | 'bookmark' | 'share' | 'arrow-left' | 'arrow-right' | 'external-link' | 'shield-check' | 'git-compare' | 'sun';
  interface Props { name: IconName; size?: number; strokeWidth?: number; fill?: string; color?: string; label?: string; }
  let { name, size = 20, strokeWidth = 2, fill = 'none', color = 'currentColor', label }: Props = $props();
  const icons: Record<IconName, Component> = {search: Search, x: X, 'chevron-down': ChevronDown, check: Check, plus: Plus, loader: LoaderCircle, user: User, trash: Trash2, edit: Pencil, bookmark: Bookmark, share: Share2, 'arrow-left': ArrowLeft, 'arrow-right': ArrowRight, 'external-link': ExternalLink, 'shield-check': ShieldCheck, 'git-compare': GitCompare, sun: Sun};
  let Selected = $derived(icons[name]);
</script>
<Selected {size} {strokeWidth} {fill} {color} aria-label={label} aria-hidden={label ? undefined : 'true'} />
