<script lang="ts">
  import { onMount } from 'svelte';
  import { ApiError, request, mutate, message, photoUrl, id } from './api';
  import { appState, refreshIdentity, DEFAULT_SITE_COPY, setSiteCopy } from './app-state.svelte';
  import PhotoDate from './PhotoDate.svelte';
  import Spinner from './ui/Spinner.svelte';
  import Button from './ui/Button.svelte';
  import Icon, { type IconName } from './ui/Icon.svelte';
  import Modal from './ui/Modal.svelte';
  import UserPicker from './UserPicker.svelte';
  import type { AdminPhoto, AdminUser, User, SiteCopy, SiteSettings } from './types';

  const identity = $derived(appState.identity);
  const sections: {key: string; label: string; icon: IconName}[] = [
    {key: 'photos', label: 'Photos', icon: 'shield-check'},
    {key: 'users', label: 'Profiles', icon: 'user'},
    {key: 'copy', label: 'Site text', icon: 'edit'},
    {key: 'merge', label: 'Merge profiles', icon: 'git-compare'},
    {key: 'audit', label: 'Audit log', icon: 'bookmark'},
  ];
  let busy = $state(false), error = $state(''), notice = $state(''), tab = $state('photos');
  let photos = $state<AdminPhoto[]>([]), users = $state<AdminUser[]>([]), query = $state('');
  let usersStart = $state(0), usersLimit = $state(20), usersMore = $state(false), appliedQuery = $state('');
  let confirmation = $state<{title: string; description: string; label: string; danger?: boolean; run: () => Promise<void>} | null>(null);
  let copy = $state<SiteCopy>({...DEFAULT_SITE_COPY}), copyRevision = $state(0), copyLoaded = $state(false);
  const copyFields: {key: keyof SiteCopy; label: string; max: number; required?: boolean}[] = [
    {key:'site_title',label:'Site title',max:80,required:true}, {key:'site_tagline',label:'Site tagline',max:160},
    {key:'home_title',label:'Home title',max:120,required:true}, {key:'home_intro',label:'Home introduction',max:300}, {key:'footer_text',label:'Footer text',max:200},
  ];
  type MergeProfile = {user: User; recipes: number; photos: number; devices: number};
  type MergePreview = {source: MergeProfile; target: MergeProfile; result: {trusted: boolean; state: string}};
  let source = $state<AdminUser | null>(null), target = $state<AdminUser | null>(null);
  let preview = $state<MergePreview>(), previewKey = $state(''), mergeConfirmed = $state(false), audit = $state<Record<string, unknown>[]>([]);

  onMount(() => {void action(async () => {await refreshIdentity(); if (appState.identity?.admin) await loadTab('photos');});});
  async function action(fn: () => Promise<void>) {
    if (busy) return;
    busy = true; error = ''; notice = '';
    try {await fn();} catch(e) {error = message(e);} finally {busy = false;}
  }
  async function loadUsers(start = 0, search = appliedQuery) {
    const result = await request<{users: AdminUser[]; start: number; limit: number; has_more: boolean}>(`/admin/users?q=${encodeURIComponent(search)}&start=${start}&limit=${usersLimit}`);
    users = result.users; usersStart = result.start; usersLimit = result.limit; usersMore = result.has_more; appliedQuery = search;
  }
  async function loadTab(value: string) {
    tab = value;
    if (value === 'photos') photos = (await request<{photos: AdminPhoto[]}>('/admin/photos')).photos;
    else if (value === 'users') await loadUsers(0, query);
    else if (value === 'copy' && !copyLoaded) await loadCopy();
    else if (value === 'audit') audit = (await request<{events: Record<string, unknown>[]}>('/admin/audit')).events;
  }
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
  async function moderate(photo: AdminPhoto, state: AdminPhoto['status']) {const result = await mutate<AdminPhoto>(`/admin/photos/${id(photo.id)}/moderate`, {state}); photo.status = result.status; notice = `Photo ${state}.`;}
  async function updateUser(user: AdminUser, changes: Partial<User>) {await mutate(`/admin/users/${id(user.id)}`, changes, 'PATCH'); Object.assign(user, changes); notice = 'Profile updated. Historical photos are not automatically approved or hidden.';}
  function promote(user: AdminUser) {
    confirmation = {
      title: `Make ${user.display_name} an admin?`, label: 'Make admin',
      description: 'This profile and all its connected devices will be able to moderate photos, manage profiles, grant admin access, merge profiles, and change site text. Only grant access to someone you trust.',
      run: async () => {
        await mutate(`/admin/users/${id(user.id)}`, {is_admin: true}, 'PATCH');
        await loadUsers(usersStart);
        notice = `${user.display_name} is now an admin.`;
      },
    };
  }
  function changeUserState(user: AdminUser) {
    const state = user.state === 'blocked' ? 'active' : 'blocked';
    confirmation = {title: `${state === 'active' ? 'Unblock' : 'Block'} ${user.display_name}?`, label: state === 'active' ? 'Unblock profile' : 'Block profile & devices', danger: state === 'blocked', description: state === 'active' ? 'This profile and its connected devices will be able to contribute again.' : 'This blocks contributions from this profile and all its connected devices. Existing photos are unchanged.', run: () => updateUser(user, {state})};
  }
  function moderateUserPhotos(user: AdminUser, state: 'pending' | 'approved') {
    const approving = state === 'approved';
    confirmation = {
      title: `${approving ? 'Approve pending' : 'Hide approved'} photos from ${user.display_name}?`, label: approving ? 'Approve pending photos' : 'Hide approved photos', danger: !approving,
      description: 'This applies to currently listed photos only. Trust for future uploads is unchanged.',
      run: async () => {
        const listed = (await request<{photos: AdminPhoto[]}>('/admin/photos')).photos.filter(photo => photo.uploader_id === user.id && photo.status === (approving ? 'pending' : 'approved'));
        for (const photo of listed) await moderate(photo, state);
        notice = `${listed.length} listed photos ${approving ? 'approved' : 'hidden'}. Refresh moderation for any further pages.`;
      },
    };
  }
  async function previewMerge() {
    if (!source || !target) return;
    const sourceId = source.id, targetId = target.id;
    preview = undefined; mergeConfirmed = false;
    const result = await request<MergePreview>(`/admin/merge/preview?source_id=${id(sourceId)}&target_id=${id(targetId)}`);
    if (sourceId !== source?.id || targetId !== target?.id) return;
    preview = result; previewKey = `${sourceId}:${targetId}`;
  }
  function confirmMerge() {
    if (!source || !target) return;
    const sourceId = source.id, targetId = target.id;
    confirmation = {title: `Merge ${source.display_name} into ${target.display_name}?`, label: 'Merge profiles', danger: true,
      description: 'Recipes, photos, and connected devices will move to the surviving profile. Its admin permission remains unchanged. This cannot be undone with the profile editor.',
      run: async () => {await mutate('/admin/merge', {source_id: sourceId, target_id: targetId, confirm: true}); preview = undefined; source = null; target = null; mergeConfirmed = false; notice = 'Profiles merged. Review the audit log.';},
    };
  }
</script>

<div class="admin-workspace">
  <header class="admin-heading"><div class="heading-icon"><Icon name="shield-check" size={28} /></div><div><h1>Admin</h1><p>Manage your community and keep the recipe collection welcoming.</p></div></header>
  <p class="access-note">Admin access belongs to your current profile, including its connected devices.</p>
  {#if error}<p role="alert" class="notice error">{error}</p>{/if}
  {#if notice}<p role="status" class="notice">{notice}</p>{/if}
  {#if busy}<Spinner label="Loading admin changes" />{/if}
  {#if identity && !identity.admin}
    <p class="notice">Your current profile does not have admin access.</p><Button variant="secondary" size="sm" href="/profile"><Icon name="user" size={16} />Go to profile</Button>
  {:else if identity?.admin}
    <nav class="admin-nav" aria-label="Admin sections">
      {#each sections as section}<button class:current={tab === section.key} aria-pressed={tab === section.key} disabled={busy} onclick={() => void action(() => loadTab(section.key))}><Icon name={section.icon} size={20} /><span>{section.label}</span></button>{/each}
    </nav>
    <div class="admin-content" aria-busy={busy}>
      {#if tab === 'photos'}
        <h2>Photo moderation</h2><p class="section-intro">Approve pending photos individually. Trust applies to future uploads and views, not existing photos.</p>
        <div class="photo-grid">{#each photos as photo}<figure class="admin-card">
          <a href={photoUrl(photo.id)} target="_blank" rel="noopener" aria-label="Open full-size photo">
            <img src={photoUrl(photo.id, true)} alt="Contribution awaiting review" width="280" height="210" loading="lazy">
          </a>
          <figcaption>
            <div class="card-heading"><strong>{photo.uploader_name || 'Unknown contributor'}</strong><span class="badge">{photo.status}</span></div>
            <PhotoDate createdAt={photo.created_at} />
            {#if photo.caption}<p class="prose">{photo.caption}</p>{/if}
            <div class="photo-actions"><Button variant="ghost" size="sm" href={`/recipes/${id(photo.recipe_id)}`}><Icon name="arrow-right" size={16} />View recipe</Button>
              <div class="toolbar">
                {#each ['approved','rejected','pending'] as const as state}<Button size="sm" variant={state === 'approved' ? 'primary' : 'secondary'} disabled={busy || photo.status === state} onclick={() => void action(() => moderate(photo, state))}><Icon name={state === 'approved' ? 'check' : state === 'rejected' ? 'x' : 'arrow-left'} size={16} />{state === 'approved' ? 'Approve' : state === 'rejected' ? 'Reject' : 'Return to pending'}</Button>{/each}
              </div>
              {#if photo.uploader_id}<Button size="sm" disabled={busy} onclick={() => {const uploaderId = photo.uploader_id!; confirmation = {title: `Trust ${photo.uploader_name || 'this contributor'}?`, label: 'Trust user', description: 'Future photos can be approved automatically and future views will be classified as trusted. Existing pending photos still need approval.', run: async () => {await mutate(`/admin/users/${id(uploaderId)}`, {trusted: true}, 'PATCH'); notice = 'User trusted for future photos and views.';}};}}><Icon name="shield-check" size={16} />Trust user</Button>{/if}
            </div>
            <details class="technical"><summary>Photo details</summary><dl><dt>Photo ID</dt><dd><code>{photo.id}</code></dd><dt>Recipe ID</dt><dd><code>{photo.recipe_id}</code></dd>{#if photo.uploader_id}<dt>Profile ID</dt><dd><code>{photo.uploader_id}</code></dd>{/if}</dl></details>
          </figcaption>
        </figure>{/each}</div>
        {#if !busy && !photos.length}<p class="empty-state">No photos to review.</p>{/if}
      {:else if tab === 'users'}
        <h2>Profiles</h2><p class="section-intro">Manage contribution permissions and admin access. Merged profiles appear under their surviving profile.</p>
        <form class="profile-search" onsubmit={(event) => {event.preventDefault(); void action(() => loadUsers(0, query));}}>
          <label>Find a profile<input bind:value={query} placeholder="Search by name or profile ID" disabled={busy}></label>
          <Button type="submit" disabled={busy}><Icon name="search" size={18} />Find profiles</Button>
        </form>
        <div class="profile-grid">{#each users as user (user.id)}<section class="admin-card profile-card" aria-label={user.display_name}>
          <div class="card-heading"><h3><Icon name="user" size={20} />{user.display_name}</h3>{#if user.is_admin}<span class="badge admin-badge"><Icon name="shield-check" size={15} />Admin</span>{/if}</div>
          <div class="badges"><span class="badge" class:blocked={user.state === 'blocked'}>{user.state}</span><span class="badge">{user.trusted ? 'Trusted user' : 'Untrusted user'}</span></div>
          <div class="toolbar profile-actions">
            {#if user.state === 'active' && !user.is_admin}<Button size="sm" disabled={busy} onclick={() => promote(user)}><Icon name="shield-check" size={16} />Make admin</Button>{/if}
            <Button size="sm" disabled={busy} onclick={() => void action(() => updateUser(user, {trusted: !user.trusted}))}>{user.trusted ? 'Revoke trust' : 'Trust user'}</Button>
            <Button size="sm" variant={user.state === 'blocked' ? 'secondary' : 'danger'} disabled={busy} onclick={() => changeUserState(user)}>{user.state === 'blocked' ? 'Unblock' : 'Block profile & devices'}</Button>
          </div>
          <details class="profile-tools"><summary>Existing photo moderation</summary><p>Apply changes to this contributor’s currently listed photos. Future upload trust is unchanged.</p><div class="toolbar">
            <Button size="sm" disabled={busy} onclick={() => moderateUserPhotos(user, 'pending')}>Hide existing approved photos</Button>
            <Button size="sm" disabled={busy} onclick={() => moderateUserPhotos(user, 'approved')}>Approve existing pending photos</Button>
          </div></details>
          <details class="technical"><summary>Profile IDs{#if user.merged_user_ids?.length} · {user.merged_user_ids.length} merged{/if}</summary><dl><dt>Profile ID</dt><dd><code>{user.id}</code></dd></dl>
            {#if user.merged_user_ids?.length}<h4>Merged profile IDs</h4><ul>{#each user.merged_user_ids as mergedId}<li><code>{mergedId}</code></li>{/each}</ul>{:else}<p>No merged profiles.</p>{/if}
          </details>
        </section>{/each}</div>
        {#if !busy && !users.length}<p class="empty-state">No profiles found. Try another name or profile ID.</p>{/if}
        <nav class="pagination" aria-label="Profile pages"><Button size="sm" disabled={busy || usersStart === 0} onclick={() => void action(() => loadUsers(Math.max(0, usersStart - usersLimit)))}><Icon name="arrow-left" size={16} />Previous</Button><span>{users.length ? `Profiles ${usersStart + 1}–${usersStart + users.length}` : 'No profiles'}</span><Button size="sm" disabled={busy || !usersMore} onclick={() => void action(() => loadUsers(usersStart + usersLimit))}>Next<Icon name="arrow-right" size={16} /></Button></nav>
      {:else if tab === 'copy'}
        <h2>Site text</h2><p class="section-intro">Change public headings and supporting text without affecting recipes.</p>
        {#if copyLoaded}<div class="copy-layout"><form class="admin-card copy-form" onsubmit={(event) => {event.preventDefault(); void saveCopy();}}>
          {#each copyFields as field}<label>{field.label}<input bind:value={copy[field.key]} maxlength={field.max} required={field.required} disabled={busy}></label>{/each}
          <div class="toolbar"><Button type="submit" variant="primary" disabled={busy || !copy.site_title.trim() || !copy.home_title.trim()}><Icon name="check" size={16} />Save site text</Button><Button disabled={busy} onclick={() => copy = {...DEFAULT_SITE_COPY}}>Reset to defaults</Button></div>
        </form><section class="admin-card copy-preview" aria-label="Site text preview"><h3>Preview</h3><strong>{copy.site_title}</strong>{#if copy.site_tagline}<p>{copy.site_tagline}</p>{/if}<h4>{copy.home_title}</h4>{#if copy.home_intro}<p>{copy.home_intro}</p>{/if}{#if copy.footer_text}<p>{copy.footer_text}</p>{/if}</section></div>{/if}
        <Button variant="ghost" disabled={busy} onclick={() => void action(loadCopy)}>Reload saved text (discard edits)</Button>
      {:else if tab === 'merge'}
        <h2>Merge profiles</h2><p class="section-intro">Verify ownership with the people involved outside this website first. The source joins the surviving target profile. The target’s admin permission remains unchanged.</p>
        <form class="admin-card" onsubmit={(event) => {event.preventDefault(); void action(previewMerge);}}><div class="merge-users">
          <UserPicker label="Source profile (will merge into target)" selectedId={source?.id} selectedName={source?.display_name} disabled={busy} eligibility="merge" excludeId={target?.id} help="Search by profile name or ID." optionsLabel="Source profiles" onselect={(user) => {source = user; preview = undefined; mergeConfirmed = false;}} />
          <UserPicker label="Target profile (survives)" selectedId={target?.id} selectedName={target?.display_name} disabled={busy} eligibility="merge" excludeId={source?.id} help="Search by profile name or ID." optionsLabel="Target profiles" onselect={(user) => {target = user; preview = undefined; mergeConfirmed = false;}} />
        </div><Button type="submit" disabled={busy || !source || !target || source.id === target.id}><Icon name="git-compare" size={16} />Preview merge</Button></form>
        {#if preview}<section class="admin-card merge-preview"><h3>Merge preview</h3><div class="merge-users">
          {#each [{label: 'Source', profile: preview.source}, {label: 'Surviving profile', profile: preview.target}] as item}<div><h4>{item.label}: {item.profile.user.display_name}</h4><p>{item.profile.recipes} recipes · {item.profile.photos} photos · {item.profile.devices} devices</p><p>{item.profile.user.state} · {item.profile.user.trusted ? 'Trusted' : 'Untrusted'}</p></div>{/each}
        </div><p><strong>Result:</strong> {preview.result.state} · {preview.result.trusted ? 'Trusted' : 'Untrusted'}. The target profile’s admin permission remains unchanged.</p>
          <details class="technical"><summary>Technical merge details</summary><pre>{JSON.stringify(preview, null, 2)}</pre></details>
          <label class="inline merge-check"><input type="checkbox" bind:checked={mergeConfirmed} disabled={busy}>I verified ownership and reviewed recipe, photo, device, and trust/block changes</label>
          <Button variant="danger" disabled={busy || !mergeConfirmed || previewKey !== `${source?.id}:${target?.id}`} onclick={confirmMerge}>Confirm profile merge</Button>
        </section>{/if}
      {:else if tab === 'audit'}
        <div class="card-heading"><h2>Audit events</h2><Button size="sm" disabled={busy} onclick={() => void action(() => loadTab('audit'))}>Refresh</Button></div><p class="section-intro">Review administrative changes. Expand an event for its full record.</p>
        <div class="audit-list">{#each audit as event}<details class="admin-card audit-event"><summary><strong>{String(event.action || 'Admin event')}</strong>{#if event.created_at}<span>{new Date(String(event.created_at)).toLocaleString()}</span>{/if}</summary><pre class="audit">{JSON.stringify(event, null, 2)}</pre></details>{/each}</div>
        {#if !busy && !audit.length}<p class="empty-state">No audit events returned.</p>{/if}
      {/if}
    </div>
  {:else}<p role="status">Checking admin access…</p>{/if}
</div>
<Modal open={!!confirmation} title={confirmation?.title || 'Confirm admin action'} onclose={() => confirmation = null}>
  <p>{confirmation?.description}</p><div class="toolbar"><Button disabled={busy} onclick={() => confirmation = null}>Cancel</Button><Button variant={confirmation?.danger ? 'danger' : 'primary'} disabled={busy} onclick={() => {const pending = confirmation; if (!pending || busy) return; confirmation = null; void action(pending.run);}}>{confirmation?.label || 'Confirm'}</Button></div>
</Modal>

<style>
  .admin-workspace{max-width:76rem;margin-inline:auto;padding-bottom:2rem}
  .admin-heading{display:flex;align-items:center;gap:1rem;margin-bottom:.5rem}.admin-heading h1{margin:0}.admin-heading p{margin:.35rem 0 0;color:var(--ui-text-muted)}.heading-icon{display:grid;place-items:center;padding:.85rem;border-radius:.85rem;background:var(--ui-surface-muted);color:var(--ui-accent)}.access-note{font-size:.9rem;color:var(--ui-text-muted)}
  .admin-nav{display:flex;flex-wrap:wrap;gap:.4rem;padding:.5rem;background:var(--ui-surface-muted);border:1px solid var(--ui-control-border);border-radius:.85rem;margin:1.5rem 0}.admin-nav button{display:flex;align-items:center;justify-content:center;gap:.5rem;flex:1 1 auto;min-height:44px;padding:.7rem .85rem;border:1px solid transparent;background:transparent;color:var(--ui-text);font-weight:650;border-radius:.5rem}.admin-nav button.current{background:var(--ui-surface);border-color:var(--ui-control-border);color:var(--ui-accent)}
  .admin-content h2{margin-top:0}.section-intro{color:var(--ui-text-muted);max-width:65ch;margin-bottom:1.5rem}.admin-card{padding:1.15rem;border:1px solid var(--ui-control-border);border-radius:.8rem;background:var(--ui-surface);min-width:0}.card-heading{display:flex;align-items:center;justify-content:space-between;gap:.75rem;flex-wrap:wrap}.card-heading h3{display:flex;align-items:center;gap:.5rem;margin:0;overflow-wrap:anywhere}.card-heading strong{overflow-wrap:anywhere}.badge{display:inline-flex;align-items:center;gap:.3rem;padding:.2rem .6rem;border-radius:1rem;font-size:.8rem;font-weight:650;background:var(--ui-surface-muted);color:var(--ui-text)}.admin-badge{color:var(--ui-accent);border:1px solid currentColor}.blocked{border:1px solid currentColor}.badges{display:flex;flex-wrap:wrap;gap:.4rem;margin:.85rem 0}.toolbar{gap:.5rem}.profile-actions{margin:1rem 0}.profile-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,25rem),1fr));gap:1rem}.profile-search{display:flex;align-items:end;gap:.75rem;margin-bottom:1.25rem}.profile-search label{flex:1;margin:0}.profile-search input{width:100%;margin-bottom:0}.pagination{display:flex;align-items:center;justify-content:center;gap:1rem;flex-wrap:wrap;margin-top:1.5rem}.pagination span{font-size:.9rem;color:var(--ui-text-muted)}
  .technical,.profile-tools{margin-top:1rem;border-top:1px solid var(--ui-control-border);padding-top:.75rem}.technical summary,.profile-tools summary{cursor:pointer;font-weight:600;min-height:32px;overflow-wrap:anywhere}.technical{font-size:.85rem;color:var(--ui-text-muted)}.technical code{overflow-wrap:anywhere;white-space:normal}.technical dd{margin:.25rem 0 .75rem}.technical h4{margin-bottom:.4rem}.technical ul{padding-left:1.25rem}.technical pre,.audit{max-width:100%;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}.profile-tools p{font-size:.9rem;color:var(--ui-text-muted)}
  .photo-grid{gap:1rem}.photo-grid figure{margin:0;overflow:hidden;padding:0}.photo-grid figure>a{display:block}.photo-grid img{display:block;width:100%;object-fit:cover}.photo-grid figcaption{padding:1rem}.photo-actions{display:grid;gap:.65rem}.empty-state{padding:2rem;text-align:center;background:var(--ui-surface-muted);border-radius:.75rem;color:var(--ui-text-muted)}
  .copy-layout{display:grid;grid-template-columns:minmax(0,1.2fr) minmax(0,1fr);gap:1rem;margin-bottom:1rem}.copy-form input{width:100%}.copy-preview{align-self:start}.copy-preview h3{margin-top:0}.merge-users{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,20rem),1fr));gap:1rem;margin-bottom:1rem}.merge-preview{margin-top:1rem}.merge-check{margin:1rem 0}.audit-list{display:grid;gap:.65rem}.audit-event summary{cursor:pointer;display:flex;justify-content:space-between;gap:.75rem;flex-wrap:wrap;min-height:32px}.audit-event summary span{color:var(--ui-text-muted);font-size:.85rem}
  @media(max-width:600px){.heading-icon{display:none}.admin-nav button{flex-basis:calc(50% - .4rem);justify-content:flex-start}.profile-search{align-items:stretch;flex-direction:column}.copy-layout{grid-template-columns:1fr}.admin-card{padding:1rem}.pagination{gap:.6rem}}
</style>
