<script lang="ts">
  import { onMount } from 'svelte';
  import { ApiError, request, mutate, message, id } from './api';
  import { appState, refreshIdentity } from './app-state.svelte';
  import IdentityPrompt from './IdentityPrompt.svelte';
  import Modal from './ui/Modal.svelte';
  import Spinner from './ui/Spinner.svelte';
  import type { Device, Pairing, RecipeSummary } from './types';
  let { token = '', consumeToken }: {token?: string; consumeToken: () => void} = $props();
  const identity = $derived(appState.identity);
  let revokeDevice = $state<Device | null>(null), devicesOpen = $state(false);
  let devices = $state<Device[]>([]), recipes = $state<RecipeSummary[]>([]);
  let name = $state(''), code = $state(''), error = $state(''), notice = $state(''), busy = $state(false), section = $state('profile');
  let origin = $state<Pairing>(), destination = $state<Pairing>(), qr = $state(''), switchConfirmed = $state(false);
  let recipeOffset = $state(0), recipesMore = $state(false);
  let secret = $state(''), pollBusy = false, alive = true;
  onMount(() => {secret = token; devicesOpen = !!token; consumeToken(); void refresh(); const timer = setInterval(() => void poll(), 2500); return () => {clearInterval(timer); alive = false; secret = '';};});
  async function refresh() { error = ''; try {const current = await refreshIdentity(); name = current.user?.display_name || ''; if (!alive) return; if (current.user) {devices = (await request<{devices: Device[]}>('/devices')).devices; await myRecipes();} else {devices = []; recipes = [];} } catch(e) {error = message(e);} }
  async function action(fn: () => Promise<void>) { busy = true; error = ''; try {await fn();} catch(e) {error = message(e);} finally {busy = false;} }
  async function startPairing() { await action(async () => {origin = await mutate<Pairing>('/pairings'); const issued = origin; const QR = await import('qrcode'); qr = await QR.toDataURL(`${location.origin}/profile#pair=${encodeURIComponent(issued.token!)}`, {width: 220, margin: 2});}); }
  async function requestPairing() { await action(async () => {destination = await mutate<Pairing>('/pairings/request', secret ? {token: decodeURIComponent(secret)} : {code: code.trim()}); secret = ''; code = ''; notice = 'Request sent. Confirm this connection on your existing device before completing here.';}); }
  async function poll() {
    if (pollBusy || !alive) return;
    const terminal = ['consumed','expired','revoked','unavailable'];
    const target = [destination, origin].find(pairing => pairing && !terminal.includes(pairing.status || ''));
    if (!target) return;
    const previousStatus = target.status;
    if (target.expires_at && Date.parse(target.expires_at) <= Date.now()) { target.status = 'expired'; qr = ''; return; }
    pollBusy = true;
    try {const result = await request<Pairing>(`/pairings/${id(target.id)}`); if (!alive || target.status !== previousStatus) return; if (origin?.id === target.id) origin = {...origin, ...result}; else if (destination?.id === target.id) destination = {...destination, ...result};}
    catch(e) {if (alive) {if (target.status !== previousStatus) return; if (e instanceof ApiError && e.status === 404) {target.status = 'unavailable'; qr = ''; notice = 'This pairing is no longer available. It may have completed, expired, or been revoked.';} else error = message(e);}} finally {pollBusy = false;}
  }
  async function myRecipes(append = false) {if (!identity?.user) return; if (!append) {recipes = []; recipeOffset = 0;} await action(async () => {const result = await request<{items: RecipeSummary[]; has_more: boolean}>(`/recipes?owner_id=${id(identity!.user!.id)}&offset=${recipeOffset}&limit=100`); recipes = [...recipes, ...result.items.filter(recipe => recipe.owner_id === identity?.user?.id)]; recipeOffset += result.items.length; recipesMore = result.has_more;});}
</script>
<h1>{identity?.user?.display_name || 'Your profile'}</h1>
<p>Manage your public name and recipes. Saved recipes and drafts stay on this device.</p>
{#if busy}<Spinner label="Updating profile" />{/if}
{#if error}<p role="alert" class="notice error">{error}</p><button type="button" disabled={busy} onclick={() => void refresh()}>Reload profile</button>{/if}{#if notice}<p role="status" class="notice">{notice}</p>{/if}
{#if identity}
{#if identity.user}<nav class="toolbar" aria-label="Profile sections">
<button onclick={() => section = 'profile'}>My profile</button>
<button onclick={() => {section = 'profile'; void myRecipes();}}>My recipes</button>
<button onclick={() => section = 'photos'}>My photo submissions</button>
{#if identity.admin}<a href="/admin">Admin</a>{/if}
</nav>
{#if section === 'profile'}<form onsubmit={(event) => {event.preventDefault(); void action(async () => {await mutate('/identity', {display_name: name.trim()}, 'PATCH'); await refresh(); notice = 'Public display name updated.';});}}>
<label>Public display name<input bind:value={name} disabled={busy} required maxlength="80" autocomplete="nickname">
</label>
<p class="help">Names are public and not verified.</p>
<button disabled={busy}>Save name</button>
</form>
<section><h2>My recipes</h2>{#each recipes.filter(recipe => recipe.owner_id === identity?.user?.id) as recipe}<p><a href={`/recipes/${id(recipe.id)}`}>{recipe.title}</a></p>{/each}{#if !busy && !recipes.length}<p>No published recipes yet. <a href="/new">Add a recipe</a></p>{/if}{#if recipesMore}<button disabled={busy} onclick={() => myRecipes(true)}>Load more of my recipes</button>{/if}</section>
<details bind:open={devicesOpen}><summary>Devices</summary>
<p>This browser remembers your profile without a password. Clearing browser data can lose access. Connect another device while you still have this one. Connected devices share this profile’s permissions, including admin access if granted.</p>
<section>
<h2>Connected devices</h2>{#each devices.filter(device => !device.revoked_at) as device}<div class="list-row">
<span>{device.label || 'Browser'} {device.id === identity.device_id ? '(this device)' : ''}<small>Last used: {device.last_used_at ? new Date(device.last_used_at).toLocaleString() : 'unknown'}</small>
</span>
<button disabled={busy} onclick={() => revokeDevice = device}>Revoke</button>
</div>{/each}</section>
<section>
<h2>Connect another device</h2>
<p>Create a short-lived code on this device. Enter it or scan the QR on your other device, then come back here to confirm.</p>
<button disabled={busy || !!origin && !['expired','consumed','revoked','unavailable'].includes(origin.status || '')} onclick={startPairing}>Create pairing code</button>
{#if origin}<div class="notice">
<p>Code: <strong class="pair-code">{origin.code}</strong>
</p>{#if qr && origin.status !== 'expired'}<img src={qr} width="220" height="220" alt="Scan to request a connection to this profile">{/if}<p>Expires: {origin.expires_at ? new Date(origin.expires_at).toLocaleTimeString() : 'in about five minutes'}</p>
<p role="status">Status: {origin.status || 'waiting for a request'}</p>{#if origin.status === 'requested'}<p>Only confirm if you just requested this connection from your other device.</p>
<button class="primary" disabled={busy} onclick={() => {if (confirm('Confirm that this is your other device requesting access?')) void action(async () => {const result = await mutate<Pairing>(`/pairings/${id(origin!.id)}/confirm`); origin = {...origin!, ...result}; notice = 'Confirmed. Complete the connection on the other device.';});}}>Confirm this device request</button>{/if}</div>{/if}</section>
</details>
{:else if section === 'photos'}{#await import('./Photos.svelte')}<p>Loading submissions…</p>{:then {default: Photos}}<Photos mine />{:catch}<p role="alert">Could not load submissions.</p>{/await}{/if}
{:else}<IdentityPrompt onready={() => void refresh()} />{/if}
<details open={!!secret || !!destination}><summary>Connect an existing profile</summary><section class="pair-destination">
<h2>Use an existing profile</h2>
<p>Request access with a code from your existing device. Scanning a link alone never changes your identity.</p>{#if !destination}<form onsubmit={(event) => {event.preventDefault(); void requestPairing();}}>{#if secret}<p>A pairing link is ready. Its private token has been removed from the address bar.</p>{:else}<label>Pairing code<input bind:value={code} required autocomplete="off" autocapitalize="characters" spellcheck="false">
</label>{/if}<button disabled={busy || (!secret && !code.trim())}>Request connection</button>
</form>
{:else}<p>Requested profile: <strong>{destination.display_name || 'Waiting for confirmation'}</strong>
</p>
<p role="status">Status: {destination.status || 'pending'}</p>{#if destination.status === 'approved'}<div class="notice">
<p>{identity.user || destination.has_existing_profile ? 'You already have a profile. Switching will NOT merge or move your current recipes or photos. Keep another connected device for your current profile; ask an admin if you need the profiles merged.' : 'This browser will use the confirmed profile for future contributions.'}</p>
<label class="inline">
<input type="checkbox" bind:checked={switchConfirmed}>I want to switch this browser to this profile</label>
<button class="primary" disabled={busy || !switchConfirmed} onclick={() => void action(async () => {await mutate(`/pairings/${id(destination!.id)}/complete`, {switch_profile: true}); destination = {...destination!, status: 'consumed'}; await refresh(); notice = 'This device is connected and now uses the selected profile’s permissions.';})}>Complete connection</button>
</div>{/if}{/if}</section></details>
{:else if !error}<Spinner label="Loading profile" />{/if}
<Modal open={!!revokeDevice} title="Revoke device access?" onclose={() => revokeDevice = null}>
<p>{revokeDevice?.id === identity?.device_id ? 'This browser will lose access to your profile. If no other device is connected, you may be unable to recover it.' : 'This device will no longer be able to use your profile.'} Local drafts and saved recipes will not be deleted.</p>
<button type="button" class="danger" disabled={busy} onclick={() => void action(async () => {if (!revokeDevice) return; const deviceId = revokeDevice.id; await mutate(`/devices/${id(deviceId)}`, undefined, 'DELETE'); revokeDevice = null; await refresh();})}>Revoke access</button><button type="button" disabled={busy} onclick={() => revokeDevice = null}>Cancel</button>
</Modal>
