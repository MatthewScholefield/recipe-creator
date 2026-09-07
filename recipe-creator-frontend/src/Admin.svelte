<script lang="ts">
  import { onMount } from 'svelte';
  import { request, mutate, session, message, photoUrl, id } from './api';
  import type { Session, AdminPhoto, User } from './types';
  interface Revision {id: string; revision?: number; created_at?: string; content?: {title?: string}}
  let identity = $state<Session>(), password = $state(''), busy = $state(false), error = $state(''), notice = $state(''), tab = $state('photos');
  let photos = $state<AdminPhoto[]>([]), users = $state<User[]>([]), query = $state(''), recipeId = $state(''), ownerId = $state(''), revision = $state(1), revisions = $state<Revision[]>([]);
  let sourceId = $state(''), targetId = $state(''), preview = $state<Record<string, unknown>>(), previewKey = $state(''), mergeConfirmed = $state(false), audit = $state<Record<string, unknown>[]>([]);
  onMount(() => {void action(async () => {identity = await session(); if (identity.admin) await loadTab('photos');});});
  async function action(fn: () => Promise<void>) {busy = true; error = ''; notice = ''; try {await fn();} catch(e) {error = message(e);} finally {busy = false;} }
  async function loadTab(value: string) {tab = value; if (value === 'photos') photos = (await request<{photos: AdminPhoto[]}>('/admin/photos')).photos; else if (value === 'users') users = (await request<{users: User[]}>(`/admin/users?q=${encodeURIComponent(query)}`)).users; else if (value === 'audit') audit = (await request<{events: Record<string, unknown>[]}>('/admin/audit')).events;}
  async function moderate(photo: AdminPhoto, state: AdminPhoto['status']) {const result = await mutate<AdminPhoto>(`/admin/photos/${id(photo.id)}/moderate`, {state}); photo.status = result.status; notice = `Photo ${state}.`;}
  async function updateUser(user: User, changes: Partial<User>) {await mutate(`/admin/users/${id(user.id)}`, changes, 'PATCH'); Object.assign(user, changes); notice = 'Profile updated. Historical photos are not automatically approved or hidden.';}
</script>
<p class="eyebrow">Collection care</p>
<h1>Admin</h1>
<p>Admin access is separate from contributor profiles and is never shared through device pairing.</p>
{#if error}<p role="alert" class="notice error">{error}</p>{/if}{#if notice}<p role="status" class="notice">{notice}</p>{/if}
{#if identity && !identity.admin}<form onsubmit={(event) => {event.preventDefault(); void action(async () => {const input = password; password = ''; await mutate('/admin/login', {password: input}); identity = await session(true); await loadTab('photos');});}}>
<label>Admin password<input type="password" required bind:value={password} autocomplete="current-password">
</label>
<button class="primary" disabled={busy}>Log in</button>
</form>
{:else if identity?.admin}<div class="toolbar">
<nav class="toolbar" aria-label="Admin sections">{#each ['photos','users','recipes','merge','audit'] as item}<button aria-pressed={tab === item} disabled={busy} onclick={() => void action(() => loadTab(item))}>{item}</button>{/each}</nav>
<button disabled={busy} onclick={() => void action(async () => {await mutate('/admin/logout'); identity = await session(true);})}>Log out</button>
</div>
{#if tab === 'photos'}<h2>Photo moderation</h2>
<p>Approve each pending photo individually. Trust applies only to future contributions.</p>
<div class="photo-grid">{#each photos as photo}<figure>
<a href={photoUrl(photo.id)} target="_blank" rel="noopener">
<img src={photoUrl(photo.id, true)} alt={photo.caption || 'Photo awaiting moderation'} width="280" height="210" loading="lazy">
</a>
<figcaption>{photo.caption}<small>{photo.uploader_name || photo.uploader_id} · {photo.status}</small>
<a href={`/recipes/${id(photo.recipe_id)}`}>View recipe</a>
<div class="toolbar">{#each ['approved','rejected','pending'] as const as state}<button disabled={busy || photo.status === state} onclick={() => void action(() => moderate(photo, state))}>{state === 'approved' ? 'Approve' : state === 'rejected' ? 'Reject' : 'Return to pending'}</button>{/each}</div>{#if photo.uploader_id}<button disabled={busy} onclick={() => {if (confirm('Trust this contributor for future photos? Existing pending photos will still need approval.')) void action(async () => {await mutate(`/admin/users/${id(photo.uploader_id!)}`, {photo_trusted: true}, 'PATCH'); notice = 'Contributor trusted for future photos only.';});}}>Trust future photos</button>{/if}</figcaption>
</figure>{/each}</div>{#if !photos.length}<p>No photos to review.</p>{/if}
{:else if tab === 'users'}<h2>Profiles</h2>
<form class="toolbar" onsubmit={(event) => {event.preventDefault(); void action(() => loadTab('users'));}}>
<label>Name or profile ID<input bind:value={query}>
</label>
<button disabled={busy}>Find profiles</button>
</form>{#each users as user}<section class="notice">
<h3>{user.display_name}</h3>
<p>
<code>{user.id}</code> · {user.state} · {user.photo_trusted ? 'trusted photos' : 'approval required'}</p>
<div class="toolbar">
<button disabled={busy} onclick={() => void action(() => updateUser(user, {photo_trusted: !user.photo_trusted}))}>{user.photo_trusted ? 'Revoke photo trust' : 'Trust future photos'}</button>
<button class="danger" disabled={busy} onclick={() => {if (confirm(`${user.state === 'blocked' ? 'Unblock' : 'Block'} ${user.display_name}?`)) void action(() => updateUser(user, {state: user.state === 'blocked' ? 'active' : 'blocked'}));}}>{user.state === 'blocked' ? 'Unblock' : 'Block profile & devices'}</button>
<button disabled={busy} onclick={() => {if (confirm('Hide all currently listed photos by this contributor?')) void action(async () => {const all = (await request<{photos: AdminPhoto[]}>('/admin/photos')).photos.filter(photo => photo.uploader_id === user.id && photo.status === 'approved'); for (const photo of all) await moderate(photo, 'pending'); notice = `${all.length} listed photos hidden. Refresh moderation for any further pages.`;});}}>Hide existing approved photos</button>
<button disabled={busy} onclick={() => {if (confirm('Approve all currently listed pending photos by this contributor? This is separate from trusting future uploads.')) void action(async () => {const pending = (await request<{photos: AdminPhoto[]}>('/admin/photos')).photos.filter(photo => photo.uploader_id === user.id && photo.status === 'pending'); for (const photo of pending) await moderate(photo, 'approved'); notice = `${pending.length} listed pending photos approved.`;});}}>Approve existing pending photos</button>
</div>
</section>{/each}
{:else if tab === 'recipes'}<h2>Recipe ownership & recovery</h2>
<label>Recipe ID<input bind:value={recipeId} disabled={busy} oninput={() => revisions = []}>
</label>{#if recipeId}<p>
<a href={`/recipes/${id(recipeId)}/edit`}>Edit recipe</a> · <a href={`/recipes/${id(recipeId)}`}>View current recipe</a>
</p>{/if}<form onsubmit={(event) => {event.preventDefault(); if (confirm('Transfer recipe ownership to this exact profile ID?')) void action(async () => {await mutate(`/admin/recipes/${id(recipeId)}/owner`, {owner_id: ownerId}); notice = 'Ownership transferred.';});}}>
<label>New owner profile ID<input required bind:value={ownerId}>
</label>
<button disabled={busy || !recipeId}>Assign / transfer owner</button>
</form>
<section>
<h3>Revision history</h3>
<button disabled={busy || !recipeId} onclick={() => void action(async () => {revisions = (await request<{revisions: Revision[]}>(`/admin/recipes/${id(recipeId)}/revisions`)).revisions;})}>Load revisions (including deleted recipes)</button>
<label>Expected current revision<input type="number" min="1" required bind:value={revision} disabled={busy}>
</label>{#each revisions as entry}<div class="list-row">
<span>{entry.content?.title || 'Recipe revision'} · {entry.revision || entry.id}<small>{entry.created_at || ''}</small>
</span>
<button disabled={busy} onclick={() => {if (confirm('Restore this revision? This records a new revision and may recover a deleted recipe.')) void action(async () => {await mutate(`/admin/recipes/${id(recipeId)}/restore`, {revision_id: entry.id, expected_revision: revision}); notice = 'Revision restored. Reload the recipe before making more changes.'; revisions = [];});}}>Restore</button>
</div>{/each}</section>
{:else if tab === 'merge'}<h2>Merge profiles</h2>
<p>Verify ownership with the people involved outside this website first. The source will become the surviving target profile. Review blocked/trusted state conflicts; no admin privilege is transferred.</p>
<form onsubmit={(event) => {event.preventDefault(); void action(async () => {const source = sourceId, target = targetId; preview = undefined; mergeConfirmed = false; const result = await request<Record<string, unknown>>(`/admin/merge/preview?source_id=${id(source)}&target_id=${id(target)}`); if (source !== sourceId || target !== targetId) return; preview = result; previewKey = `${source}:${target}`;});}}>
<label>Source profile ID (will merge into target)<input required bind:value={sourceId} oninput={() => {preview = undefined; mergeConfirmed = false;}}>
</label>
<label>Target profile ID (survives)<input required bind:value={targetId} oninput={() => {preview = undefined; mergeConfirmed = false;}}>
</label>
<button disabled={busy || !sourceId || !targetId || sourceId === targetId}>Preview merge</button>
</form>{#if preview}<section class="notice">
<h3>Merge preview</h3>
<pre class="prose">{JSON.stringify(preview, null, 2)}</pre>
<label class="inline">
<input type="checkbox" bind:checked={mergeConfirmed}>I verified ownership and reviewed recipe, photo, device, and trust/block changes</label>
<button class="danger" disabled={busy || !mergeConfirmed || previewKey !== `${sourceId}:${targetId}`} onclick={() => {if (confirm('Merge these profiles now? This cannot be undone with the profile editor.')) void action(async () => {await mutate('/admin/merge', {source_id: sourceId, target_id: targetId, confirm: true}); preview = undefined; sourceId = ''; targetId = ''; notice = 'Profiles merged. Review the audit log.';});}}>Confirm profile merge</button>
</section>{/if}
{:else if tab === 'audit'}<h2>Audit events</h2>{#each audit as event}<pre class="audit">{JSON.stringify(event, null, 2)}</pre>{/each}{#if !audit.length}<p>No audit events returned.</p>{/if}{/if}
{:else}<p role="status">Checking admin access…</p>{/if}
