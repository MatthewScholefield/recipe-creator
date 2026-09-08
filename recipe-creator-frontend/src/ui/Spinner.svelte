<script lang="ts">
  import Icon from './Icon.svelte';
  interface Props { label?: string; size?: number; }
  let { label = 'Loading', size = 20 }: Props = $props();
</script>
<span class="spinner" role="status" aria-label={label}><Icon name="loader" {size} /><span class="sr-only">{label}</span></span>
<style>
  .spinner{display:inline-flex;align-items:center;justify-content:center;line-height:1}.spinner :global(svg){animation:spin .8s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.spinner :global(svg){animation:none}}
</style>
