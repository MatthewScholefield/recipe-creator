<script lang="ts">
  import { onMount } from 'svelte';
  import { ApiError, request, mutate, message, photoUrl, id } from './api';
  import { appState, refreshIdentity, DEFAULT_SITE_COPY, setSiteCopy } from './app-state.svelte';
  import PhotoDate from './PhotoDate.svelte';
  import Spinner from './ui/Spinner.svelte';
  import Button from './ui/Button.svelte';
  import Icon from './ui/Icon.svelte';
  import UserPicker from './UserPicker.svelte';
  import type { AdminPhoto, AdminUser, User, SiteCopy, SiteSettings } from './types';
  const identity = $derived(appState.identity);
  let busy = $state(false), error = $state(''), notice = $state(''), tab = $state('photos');
  let photos = $state<AdminPhoto[]>([]), users = $state<User[]>([]), query = $state('');
  let copy = $state<SiteCopy>({...DEFAULT_SITE_COPY}), copyRevision = $state(0), copyLoaded = $state(false);
  const copyFields: {key: keyof SiteCopy; label: string; max: number; required?: boolean}[] = [
    {key:'site_title',label:'Site title',max:80,required:true}, {key:'site_tagline',label:'Site tagline',max:160},
    {key:'home_title',label:'Home title',max:120,required:true}, {key:'home_intro',label:'Home introduction',max:300}, {key:'footer_text',label:'Footer text',max:200},
  ];
  async function loadCopy() {const result = await request<SiteSettings>('/site-settings'); copy = {...result.copy}; copyRevision = result.revision; copyLoaded = true;}
  async function saveCopy() {
    await action(async () => {
      try {
        const result = await mutate<SiteSettings>('/admin/site-settings', {expected_revision: copyRevision, copy: {...copy}}, 'PUT');
        copy = {...result.copy}; copyRevision = result.revision; setSiteCopy(result.copy); notice = 'Site text saved.';
      } catch(e) {
        if (e instanceof ApiError && e.status === 409) throw new Error('Site text changed elsewhere. Your edits are kept. Reload saved text to discard them and load the latest version.');
        throw e;
      }
    });
  }
  let source = $state<AdminUser | null>(null), target = $state<AdminUser | null>(null), preview = $state<Record<string, unknown>>(), previewKey = $state(''), mergeConfirmed = $state(false), audit = $state<Record<string, unknown>[]>([]);
  onMount(() => {void action(async () => {await refreshIdentity(); if (appState.identity?.admin) await loadTab('photos');});});
  async function action(fn: () => Promise<void>) {busy = true; error = ''; notice = ''; try {await fn();} catch(e) {error = message(e);} finally {busy = false;} }
  async function loadTab(value: string) {tab = value; if (value === 'photos') photos = (await request<{photos: AdminPhoto[]}>('/admin/photos')).photos; else if (value === 'users') users = (await request<{users: User[]}>(`/admin/users?q=${encodeURIComponent(query)}`)).users; else if (value === 'copy' && !copyLoaded) await loadCopy(); else if (value === 'audit') audit = (await request<{events: Record<string, unknown>[]}>('/admin/audit')).events;}
  async function moderate(photo: AdminPhoto, state: AdminPhoto['status']) {const result = await mutate<AdminPhoto>(`/admin/photos/${id(photo.id)}/moderate`, {state}); photo.status = result.status; notice = `Photo ${state}.`;}
  async function updateUser(user: User, changes: Partial<User>) {await mutate(`/admin/users/${id(user.id)}`, changes, 'PATCH'); Object.assign(user, changes); notice = 'Profile updated. Historical photos are not automatically approved or hidden.';}
</script>
<h1>Admin</h1>
<p>Manage contributions and site text. Admin access belongs to your current profile, including its connected devices.</p>
{#if error}<p role="alert" class="notice error">{error}</p>{/if}{#if notice}<p role="status" class="notice">{notice}</p>{/if}

{#if busy}<Spinner label="Loading admin changes" />{/if}
{#if identity && !identity.admin}<p class="notice">Your current profile does not have admin access.</p><Button variant="secondary" size="sm" href="/profile"><Icon name="user" size={16} />Go to profile</Button>
{:else if identity?.admin}<nav class="toolbar" aria-label="Admin sections">{#each ['photos','users','copy','merge','audit'] as item}<button aria-pressed={tab === item} disabled={busy} onclick={() => void action(() => loadTab(item))}>{item === 'copy' ? 'Site text' : item}</button>{/each}</nav>
{#if tab === 'photos'}<h2>Photo moderation</h2>
<p>Approve each pending photo individually. Trust affects future photo approvals and view classification.</p>
<div class="photo-grid">{#each photos as photo}<figure>
<a href={photoUrl(photo.id)} target="_blank" rel="noopener" aria-label="Open full-size photo">
<!-- svelte-ignore a11y_img_redundant_alt -- exact product copy distinguishes moderation thumbnails -->
<img src={photoUrl(photo.id, true)} alt="Photo awaiting moderation" width="280" height="210" loading="lazy">
</a>
<figcaption><small>{photo.uploader_name || photo.uploader_id || 'Unknown contributor'} · {photo.status}</small>
<PhotoDate createdAt={photo.created_at} />
{#if photo.caption}<p class="prose">{photo.caption}</p>{/if}
<div class="photo-actions"><Button variant="ghost" size="sm" href={`/recipes/${id(photo.recipe_id)}`}><Icon name="arrow-right" size={16} />View recipe</Button>
<div class="toolbar">{#each ['approved','rejected','pending'] as const as state}<button disabled={busy || photo.status === state} onclick={() => void action(() => moderate(photo, state))}>{state === 'approved' ? 'Approve' : state === 'rejected' ? 'Reject' : 'Return to pending'}</button>{/each}</div>{#if photo.uploader_id}<button disabled={busy} onclick={() => {if (confirm('Trust this user? Future photos can be approved automatically and future views will be classified as trusted. Existing pending photos still need approval.')) void action(async () => {await mutate(`/admin/users/${id(photo.uploader_id!)}`, {trusted: true}, 'PATCH'); notice = 'User trusted for future photos and views.';});}}>Trust user</button>{/if}</div></figcaption>
</figure>{/each}</div>{#if !busy && !photos.length}<p>No photos to review.</p>{/if}
{:else if tab === 'users'}<h2>Profiles</h2>
<form class="toolbar" onsubmit={(event) => {event.preventDefault(); void action(() => loadTab('users'));}}>
<label>Name or profile ID<input bind:value={query}>
</label>
<button disabled={busy}>Find profiles</button>
</form>{#each users as user}<section class="notice">
<h3>{user.display_name}</h3>
<p>
<code>{user.id}</code> · {user.state} · {user.trusted ? 'Trusted user' : 'Untrusted user'}</p>
<div class="toolbar">
<button disabled={busy} onclick={() => void action(() => updateUser(user, {trusted: !user.trusted}))}>{user.trusted ? 'Revoke trust' : 'Trust user'}</button>
<button class="danger" disabled={busy} onclick={() => {if (confirm(`${user.state === 'blocked' ? 'Unblock' : 'Block'} ${user.display_name}?`)) void action(() => updateUser(user, {state: user.state === 'blocked' ? 'active' : 'blocked'}));}}>{user.state === 'blocked' ? 'Unblock' : 'Block profile & devices'}</button>
<button disabled={busy} onclick={() => {if (confirm('Hide all currently listed photos by this contributor?')) void action(async () => {const all = (await request<{photos: AdminPhoto[]}>('/admin/photos')).photos.filter(photo => photo.uploader_id === user.id && photo.status === 'approved'); for (const photo of all) await moderate(photo, 'pending'); notice = `${all.length} listed photos hidden. Refresh moderation for any further pages.`;});}}>Hide existing approved photos</button>
<button disabled={busy} onclick={() => {if (confirm('Approve all currently listed pending photos by this contributor? This is separate from trusting future uploads.')) void action(async () => {const pending = (await request<{photos: AdminPhoto[]}>('/admin/photos')).photos.filter(photo => photo.uploader_id === user.id && photo.status === 'pending'); for (const photo of pending) await moderate(photo, 'approved'); notice = `${pending.length} listed pending photos approved.`;});}}>Approve existing pending photos</button>
</div>
</section>{/each}
{:else if tab === 'copy'}<h2>Site text</h2>
<p>Change only the public headings and supporting text. Recipe text is not affected.</p>
{#if copyLoaded}<form onsubmit={(event) => {event.preventDefault(); void saveCopy();}}>
{#each copyFields as field}<label>{field.label}<input bind:value={copy[field.key]} maxlength={field.max} required={field.required} disabled={busy}></label>{/each}
<div class="toolbar"><button class="primary" disabled={busy || !copy.site_title.trim() || !copy.home_title.trim()}>Save site text</button><button type="button" disabled={busy} onclick={() => copy = {...DEFAULT_SITE_COPY}}>Reset to defaults</button></div>
</form>
<section class="notice" aria-label="Site text preview"><h3>Preview</h3><strong>{copy.site_title}</strong>{#if copy.site_tagline}<p>{copy.site_tagline}</p>{/if}<h4>{copy.home_title}</h4>{#if copy.home_intro}<p>{copy.home_intro}</p>{/if}{#if copy.footer_text}<p>{copy.footer_text}</p>{/if}</section>{/if}
<button type="button" disabled={busy} onclick={() => void action(loadCopy)}>Reload saved text (discard edits)</button>
{:else if tab === 'merge'}<h2>Merge profiles</h2>
<p>Verify ownership with the people involved outside this website first. The source profile will be merged into the surviving target profile. Review blocked/trusted state conflicts; the target profile’s admin permission remains unchanged.</p>
<form onsubmit={(event) => {event.preventDefault(); void action(async () => {const sourceId = source!.id, targetId = target!.id; preview = undefined; mergeConfirmed = false; const result = await request<Record<string, unknown>>(`/admin/merge/preview?source_id=${id(sourceId)}&target_id=${id(targetId)}`); if (sourceId !== source?.id || targetId !== target?.id) return; preview = result; previewKey = `${sourceId}:${targetId}`;});}}>
<div class="merge-users">
<UserPicker label="Source profile (will merge into target)" selectedId={source?.id} selectedName={source?.display_name} disabled={busy} eligibility="merge" excludeId={target?.id} help="Search by profile name or ID." optionsLabel="Source profiles" showIds onselect={(user) => {source = user; preview = undefined; mergeConfirmed = false;}} />
<UserPicker label="Target profile (survives)" selectedId={target?.id} selectedName={target?.display_name} disabled={busy} eligibility="merge" excludeId={source?.id} help="Search by profile name or ID." optionsLabel="Target profiles" showIds onselect={(user) => {target = user; preview = undefined; mergeConfirmed = false;}} />
</div>
<button disabled={busy || !source || !target || source.id === target.id}>Preview merge</button>
</form>{#if preview}<section class="notice">
<h3>Merge preview</h3>
<pre class="prose">{JSON.stringify(preview, null, 2)}</pre>
<label class="inline">
<input type="checkbox" bind:checked={mergeConfirmed}>I verified ownership and reviewed recipe, photo, device, and trust/block changes</label>
<button class="danger" disabled={busy || !mergeConfirmed || previewKey !== `${source?.id}:${target?.id}`} onclick={() => {if (confirm('Merge these profiles now? This cannot be undone with the profile editor.')) void action(async () => {await mutate('/admin/merge', {source_id: source!.id, target_id: target!.id, confirm: true}); preview = undefined; source = null; target = null; notice = 'Profiles merged. Review the audit log.';});}}>Confirm profile merge</button>
</section>{/if}
{:else if tab === 'audit'}<h2>Audit events</h2>{#each audit as event}<pre class="audit">{JSON.stringify(event, null, 2)}</pre>{/each}{#if !busy && !audit.length}<p>No audit events returned.</p>{/if}{/if}
{:else}<p role="status">Checking admin access…</p>{/if}

<style>
  .merge-users{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,20rem),1fr));gap:1rem;margin-bottom:1rem}
</style>