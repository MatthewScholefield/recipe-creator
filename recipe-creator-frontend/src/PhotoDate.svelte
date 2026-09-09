<script lang="ts">
  let { createdAt }: {createdAt?: string | null} = $props();

  const display = $derived.by(() => {
    if (!createdAt?.trim()) return null;
    const uploaded = new Date(createdAt);
    const uploadedTime = uploaded.getTime();
    if (Number.isNaN(uploadedTime)) return null;

    const elapsed = Math.max(0, Date.now() - uploadedTime);
    let relative = 'just now';
    if (elapsed >= 60_000) {
      const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
        ['year', 365 * 24 * 60 * 60 * 1000],
        ['month', 30 * 24 * 60 * 60 * 1000],
        ['week', 7 * 24 * 60 * 60 * 1000],
        ['day', 24 * 60 * 60 * 1000],
        ['hour', 60 * 60 * 1000],
        ['minute', 60 * 1000],
      ];
      const [unit, duration] = units.find(([, threshold]) => elapsed >= threshold)!;
      relative = new Intl.RelativeTimeFormat('en', {numeric: 'always'}).format(-Math.floor(elapsed / duration), unit);
    }
    const absolute = new Intl.DateTimeFormat('en', {dateStyle: 'long', timeStyle: 'short'}).format(uploaded);
    return {relative, absolute};
  });
</script>

{#if display}
  <span class="photo-date">Uploaded <time datetime={createdAt!} title={`Uploaded ${display.absolute}`} aria-label={`Uploaded ${display.absolute}`}>{display.relative}</time></span>
{:else}
  <span class="photo-date">Upload date unavailable</span>
{/if}

<style>
  .photo-date{display:block;color:var(--ui-text-muted);font-size:.83rem;overflow-wrap:anywhere}
</style>
