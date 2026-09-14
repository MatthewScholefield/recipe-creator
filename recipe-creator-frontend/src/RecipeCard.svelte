<script lang="ts">
  import { photoUrl } from './api';
  import PhotoDate from './PhotoDate.svelte';

  let {
    title,
    href,
    description = '',
    thumbnailPhotoId = null,
    isDraft = false,
    updatedAt,
  }: {
    title: string;
    href: string;
    description?: string;
    thumbnailPhotoId?: string | null;
    isDraft?: boolean;
    updatedAt?: string;
  } = $props();
</script>

<article class:card={true} class:draft={isDraft}>
  {#if thumbnailPhotoId}<img src={photoUrl(thumbnailPhotoId, true)} alt="" width="96" height="96" loading="lazy">{/if}
  <h3><a {href}>{title}</a></h3>
  {#if isDraft && updatedAt}<p class="byline"><PhotoDate createdAt={updatedAt} label="Updated" /></p>{/if}
  {#if description}<p class="excerpt">{description}</p>{/if}
</article>

<style>
  .card{position:relative}
  .draft{border-style:dashed}
</style>
