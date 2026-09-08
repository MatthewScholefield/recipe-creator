<script lang="ts">
  import { mutate, message } from './api';
  import { refreshIdentity } from './app-state.svelte';
  import type { Session } from './types';
  import Spinner from './ui/Spinner.svelte';
  let { onready }: {onready: (session: Session) => void} = $props();
  const inputId = $props.id();
  let name = $state(''), busy = $state(false), error = $state('');
  async function create() {
    if (busy || !name.trim()) return;
    busy = true; error = '';
    try { await mutate('/identity', {display_name: name.trim()}); onready(await refreshIdentity()); }
    catch(e) {error = message(e);} finally {busy = false;}
  }
</script>
<section class="notice" aria-busy={busy}>
  <h2>A name for your contribution</h2>
  <p>Your name is public and not verified. No email or password needed. Connect another device to keep access if browser data is lost.</p>
  <label for={inputId}>Your display name</label>
  <input id={inputId} required maxlength="80" autocomplete="nickname" bind:value={name} disabled={busy}
    onkeydown={(event) => {if (event.key === 'Enter' && !event.isComposing) {event.preventDefault(); event.stopPropagation(); void create();}}}>
  <button type="button" class="primary" disabled={busy || !name.trim()} onclick={() => void create()}>Set name</button>
  {#if busy}<Spinner label="Creating profile" />{/if}
  {#if error}<p role="alert">{error}</p>{/if}
</section>
