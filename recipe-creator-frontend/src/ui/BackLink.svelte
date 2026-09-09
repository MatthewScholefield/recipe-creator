<script lang="ts">
  import Button from './Button.svelte';
  import Icon from './Icon.svelte';

  interface Props {
    href: string;
    label: string;
  }

  let { href, label }: Props = $props();
</script>

<Button variant="ghost" size="sm" {href}>
  <Icon name="arrow-left" size={16} />
  {label}
</Button>
