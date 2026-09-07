<script lang="ts">
  import { mutate, session, message } from './api';
  import type { Session } from './types';
  let { onready }: {onready: (value: Session) => void} = $props();
  let name = $state(''), busy = $state(false), error = $state('');
  async function create() { busy = true; error = ''; try { await mutate('/identity', {display_name: name.trim()}); onready(await session(true)); } catch(e) {error = message(e);} finally {busy = false;} }
</script>
<section class="notice"><h2>A name for your contribution</h2><p>Your name will be public, not a verified identity. No email or password needed. Ownership stays in this browser; connect another device to keep access if browser data is lost.</p><form onsubmit={(event) => {event.preventDefault(); void create();}}><label>Your display name<input required maxlength="80" autocomplete="nickname" bind:value={name}></label><button class="primary" disabled={busy || !name.trim()}>{busy ? 'Creating profile…' : 'Use this name'}</button></form>{#if error}<p role="alert">{error}</p>{/if}</section>
