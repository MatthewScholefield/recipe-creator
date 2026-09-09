<script lang="ts">
  import { onMount } from 'svelte';
  import { request, mutate, session, message, photoUrl, id } from './api';
  import PhotoDate from './PhotoDate.svelte';
  import IdentityPrompt from './IdentityPrompt.svelte';
  import Button from './ui/Button.svelte';
  import Icon from './ui/Icon.svelte';
  import Spinner from './ui/Spinner.svelte';
  import type { Photo, Session } from './types';

  let { recipeId, mine = false }: {recipeId?: string; mine?: boolean} = $props();
  let photos = $state<Photo[]>([]), identity = $state<Session>(), error = $state(''), status = $state(''), caption = $state('');
  let busy = $state(false), busyKind = $state<'preparing' | 'uploading' | ''>(''), progress = $state(0), preview = $state(''), compressed = $state<File>(), needsName = $state(false), dragging = $state(false);
  let fileInput = $state<HTMLInputElement>();
  const noteLength = $derived(Array.from(caption).length);
  const noteTooLong = $derived(noteLength > 5000);
  const lifetime = new AbortController();
  let controller: AbortController | undefined, uploadKey = crypto.randomUUID(), alive = true;

  onMount(() => {void reload(); return () => {alive = false; lifetime.abort(); controller?.abort(); if (preview) URL.revokeObjectURL(preview);};});
  async function reload() {try {identity = await session(); if (!alive) return; const visible = (await request<{items: Photo[]}>(`/photos?${mine ? 'mine=true' : `recipe_id=${id(recipeId!)}`}`, {signal: lifetime.signal})).items; if (!mine && identity.user) { const own = (await request<{items: Photo[]}>('/photos?mine=true', {signal: lifetime.signal})).items.filter(photo => photo.recipe_id === recipeId); if (!alive) return; photos = [...new Map([...visible, ...own].map(photo => [photo.id, photo])).values()]; } else photos = visible; } catch(e) {if (alive) error = message(e);}}
  function clearDraft() {
    compressed = undefined;
    if (preview) URL.revokeObjectURL(preview);
    preview = '';
    caption = '';
    needsName = false;
    uploadKey = crypto.randomUUID();
    if (fileInput) fileInput.value = '';
  }
  function discard() {
    if (busy) return;
    clearDraft();
    error = '';
    status = '';
  }
  function updateCaption(value: string) {
    if (value === caption) return;
    caption = value;
    uploadKey = crypto.randomUUID();
  }
  async function compress(file?: File) {
    if (!file || busy) return;
    error = ''; status = ''; compressed = undefined; caption = ''; if (preview) URL.revokeObjectURL(preview); preview = '';
    if (!['image/jpeg','image/png','image/webp'].includes(file.type)) {error = 'Choose a JPEG, PNG, or WebP image.'; return;}
    if (file.size > 30 * 1024 * 1024) {error = 'Choose an image smaller than 30 MiB.'; return;}
    busy = true; busyKind = 'preparing'; progress = 0; controller = new AbortController(); const signal = controller.signal;
    try {
      const [{default: imageCompression}, {default: workerURL}] = await Promise.all([import('browser-image-compression'), import('browser-image-compression/dist/browser-image-compression.js?url')]);
      const canvas = document.createElement('canvas'); const type = canvas.toDataURL('image/webp').startsWith('data:image/webp') ? 'image/webp' : 'image/jpeg';
      const result = await imageCompression(file, {maxSizeMB: 400 / 1024, maxWidthOrHeight: 1600, fileType: type, initialQuality: 0.82, preserveExif: false, useWebWorker: true, libURL: new URL(workerURL, location.href).href, signal, onProgress: value => {if (alive) progress = value;}});
      if (signal.aborted || !alive) return;
      if (result.size > 512 * 1024) throw new Error('This image is still too large. Choose a smaller photo.');
      compressed = new File([result], `recipe.${result.type === 'image/webp' ? 'webp' : 'jpg'}`, {type: result.type}); preview = URL.createObjectURL(compressed); uploadKey = crypto.randomUUID(); status = 'Ready to upload.';
    } catch(e) {if (alive) error = signal.aborted ? 'Photo preparation cancelled.' : message(e);}
    finally {if (alive) {busy = false; busyKind = '';}}
  }
  function dropPhoto(event: DragEvent) { event.preventDefault(); dragging = false; void compress(event.dataTransfer?.files[0]); }
  async function upload() {
    if (!compressed || !recipeId || noteTooLong) return;
    if (!identity?.user) {needsName = true; return;}
    busy = true; busyKind = 'uploading'; error = ''; controller = new AbortController();
    try {const body = new FormData(); body.set('file', compressed); body.set('caption', caption); await mutate(`/recipes/${id(recipeId)}/photos`, body, 'POST', controller.signal, uploadKey); if (!alive) return; clearDraft(); status = 'Photo submitted.'; await reload();}
    catch(e) {if (alive) error = controller.signal.aborted ? 'Upload cancelled.' : message(e);}
    finally {if (alive) {busy = false; busyKind = '';}}
  }
  async function removePhoto(photo: Photo) { if (!confirm('Delete this photo?')) return; busy = true; try {await mutate(`/photos/${id(photo.id)}`, undefined, 'DELETE'); await reload();} catch(e) {error = message(e);} finally {busy = false;} }
</script>
{#if error}<p class="notice error" role="alert">{error}</p>{/if}{#if status}<p class="notice" role="status">{status}</p>{/if}
<div class="photo-grid">{#each photos as photo (photo.id)}<figure><a href={photoUrl(photo.id)} target="_blank" rel="noopener" aria-label="Open full-size photo"><img src={photoUrl(photo.id, true)} width="280" height="210" loading="lazy" alt={'Recipe contributed by ' + (photo.uploader_name || 'a home cook')}></a><figcaption><small>From {photo.uploader_name || photo.uploader_id || 'a home cook'}</small><PhotoDate createdAt={photo.created_at} />{#if photo.caption}<p class="prose">{photo.caption}</p>{/if}{#if photo.state !== 'approved'}<strong class="badge">Private submission: {photo.state}</strong>{/if}{#if photo.can_delete || photo.uploader_id === identity?.user?.id}<div class="photo-actions"><Button variant="ghost" size="sm" ariaLabel="Delete photo" disabled={busy} onclick={() => void removePhoto(photo)}><Icon name="trash" label="Delete photo" size={16} /></Button></div>{/if}</figcaption></figure>{/each}</div>
{#if !photos.length}<p class="quiet">No photos yet.</p>{/if}
{#if !mine}<section class="uploader"><h3>Add a photo</h3>{#if !preview}<label class:dragging class="dropper" ondragover={(event) => {event.preventDefault(); dragging = true;}} ondragleave={() => dragging = false} ondrop={dropPhoto}><input bind:this={fileInput} aria-label="Add a photo" type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onchange={(event) => void compress(event.currentTarget.files?.[0])}><Icon name="plus" size={18} />{busyKind === 'preparing' ? 'Preparing photo…' : 'Drop a photo here or choose one'}<small>JPEG, PNG, or WebP</small></label>{/if}{#if busyKind === 'preparing'}<p class="preparing"><Spinner label="Preparing photo" size={16} /> Preparing photo{progress ? ` ${Math.round(progress)}%` : '…'} <Button variant="ghost" size="sm" onclick={() => controller?.abort()}>Cancel</Button></p>{/if}{#if preview}<div class="photo-draft"><img class="photo-preview" src={preview} alt="Your contribution, ready to upload" width="320" height="240"><label>Photo note (optional)<textarea rows="6" disabled={busy} value={caption} placeholder="What did you change, and how did it turn out?" oninput={(event) => updateCaption(event.currentTarget.value)}></textarea></label><small class:error-text={noteTooLong}>{noteLength.toLocaleString()} / 5,000 characters</small>{#if noteTooLong}<p class="error-text">Photo notes must be at most 5,000 characters.</p>{/if}<div class="draft-actions"><Button variant="primary" disabled={busy || noteTooLong} onclick={upload}>Submit photo</Button><Button variant="ghost" disabled={busy} onclick={discard}>Discard photo</Button>{#if busyKind === 'uploading'}<span class="uploading"><Spinner label="Uploading photo" size={16} /> Uploading photo… <Button variant="ghost" size="sm" onclick={() => controller?.abort()}>Cancel</Button></span>{/if}</div></div>{/if}{#if needsName && !identity?.user}<IdentityPrompt onready={(value) => {identity = value; needsName = false; status = 'Profile ready.';}} />{/if}</section>{/if}
<style>
  .quiet{color:var(--ui-text-muted)}.uploader{max-width:40rem;margin-top:1.25rem}.dropper{display:flex;flex-direction:column;align-items:center;gap:.3rem;padding:1rem;border:1px dashed var(--ui-control-border);border-radius:.55rem;background:var(--ui-surface-muted);cursor:pointer;text-align:center}.dropper.dragging{border-color:var(--ui-accent);background:var(--ui-surface)}.dropper input{position:absolute;width:1px;height:1px;opacity:0;pointer-events:none}.dropper small{color:var(--ui-text-muted)}.preparing,.uploading,.draft-actions{display:flex;align-items:center;flex-wrap:wrap;gap:.4rem}.photo-draft{min-width:0}.photo-preview{display:block;max-width:100%;height:auto;margin:.8rem 0}.photo-draft label{display:block;margin:.7rem 0}.error-text{color:var(--ui-danger)}.draft-actions{margin-top:.8rem}
</style>
