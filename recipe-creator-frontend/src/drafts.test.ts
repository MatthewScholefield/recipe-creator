import { beforeEach, describe, expect, it, vi } from 'vitest';
import { blank } from './recipe';
import { createDraft, deleteDraft, draftHref, EDIT_DRAFT_MAX_AGE_MS, listDrafts, migrateLegacyDraft, readDraft, readEditDraft, readLegacyDraft, recoverLegacyDraft, saveDraft, SIGNIFICANT_DRAFT_CHANGE_SIZE, subscribeDrafts } from './drafts';

beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
describe('independent local drafts', () => {
  it('creates separate drafts with titles and deletes only the target', () => {
    const a = createDraft(), b = createDraft();
    a.draft.title = 'Soup'; b.draft.title = 'Stew'; saveDraft(a); saveDraft(b);
    expect(a.id).not.toBe(b.id); expect(a.publishKey).not.toBe(a.id); expect(a.publishKey).not.toBe(b.publishKey);
    expect(listDrafts().map(item => item.title)).toEqual(expect.arrayContaining(['Soup', 'Stew']));
    expect(draftHref(b.id)).toBe(`/new?draft=${b.id}`); expect(deleteDraft(a.id)).toBe(true);
    expect(readDraft(b.id)?.draft.title).toBe('Stew'); expect(listDrafts()).toHaveLength(1);
  });
  it('ignores corrupt entries and retains exact authored input and undo', () => {
    const a = createDraft(); a.draft.source_text = '  exact\n\n'; a.undo = {...blank(), notes:'  notes\n'}; a.ingredientLines = {'legacy-row':'  2 eggs '};
    expect(saveDraft(a)).toBe(true); expect(readDraft(a.id)).toEqual(a);
    localStorage.setItem('notebook:draft:v2:bad', '{}'); localStorage.setItem('notebook:draft:v2:legacy-new', '{');
    expect(listDrafts()).toHaveLength(1); expect(readDraft('../new')).toBeNull();
  });
  it('notifies same-tab and other-tab changes and unsubscribes', () => {
    const listener = vi.fn(), off = subscribeDrafts(listener); const a = createDraft();
    expect(listener).toHaveBeenCalledTimes(1);
    window.dispatchEvent(new StorageEvent('storage', {key:`notebook:draft:v2:${a.id}`}));
    expect(listener).toHaveBeenCalledTimes(2); off(); deleteDraft(a.id); expect(listener).toHaveBeenCalledTimes(2);
  });
  it('returns an in-memory draft when storage is blocked', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('quota'); });
    const a = createDraft(false); expect(a.draft.mode).toBe('text'); expect(saveDraft(a)).toBe(false); expect(a.draft.title).toBe('');
  });
  it('can create an in-memory draft without exposing it to draft lists', () => {
    const value = createDraft(false);
    expect(readDraft(value.id)).toBeNull();
    expect(listDrafts()).toEqual([]);
  });
});
describe('edit draft retention', () => {
  const now = Date.parse('2026-09-11T12:00:00.000Z');
  const base = {...blank('structured'), title: 'Soup', directions: 'Simmer gently.'};
  const store = (draft: typeof base, saved: string) => localStorage.setItem('notebook:draft:r1', JSON.stringify({
    draft, revision: 2, key: 'edit-key', saved, base: {revision: 2, draft: base},
  }));

  it('expires an old insignificant edit but retains recent and significant edits', () => {
    const old = new Date(now - EDIT_DRAFT_MAX_AGE_MS - 1).toISOString();
    store({...base, title: 'Soups'}, old);
    expect(readEditDraft('r1', {revision: 2, draft: base}, now)).toBeNull();
    expect(localStorage.getItem('notebook:draft:r1')).toBeNull();

    store({...base, title: 'Soups'}, new Date(now - EDIT_DRAFT_MAX_AGE_MS).toISOString());
    expect(readEditDraft('r1', {revision: 2, draft: base}, now)?.draft.title).toBe('Soups');

    store({...base, directions: base.directions + 'x'.repeat(SIGNIFICANT_DRAFT_CHANGE_SIZE)}, old);
    expect(readEditDraft('r1', {revision: 2, draft: base}, now)?.draft.directions).toHaveLength(base.directions.length + SIGNIFICANT_DRAFT_CHANGE_SIZE);
  });
  it('removes an edit draft that contains no changes from its base', () => {
    store(base, new Date(now).toISOString());
    expect(readEditDraft('r1', {revision: 2, draft: base}, now)).toBeNull();
    expect(localStorage.getItem('notebook:draft:r1')).toBeNull();
  });

  it('retains old edits when their original revision is unavailable or deletion fails', () => {
    const old = new Date(now - EDIT_DRAFT_MAX_AGE_MS - 1).toISOString();
    localStorage.setItem('notebook:draft:r1', JSON.stringify({draft: {...base, title: 'Soups'}, revision: 1, key: 'edit-key', saved: old}));
    expect(readEditDraft('r1', {revision: 2, draft: base}, now)?.draft.title).toBe('Soups');

    store({...base, title: 'Soups'}, old);
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(readEditDraft('r1', {revision: 2, draft: base}, now)?.draft.title).toBe('Soups');
  });
});
describe('safe legacy migration', () => {
  const old = () => ({draft:{...blank(), title:'Old soup', source_text:'  Exact\n'}, key:'old-retry-key', undo:{...blank(), notes:' notes '}, saved:'2025-01-01T00:00:00.000Z'});
  it('is idempotent and preserves the old key, timestamp, prose and undo', () => {
    localStorage.setItem('notebook:draft:new', JSON.stringify(old()));
    expect(migrateLegacyDraft()).toBe(true); const value = readDraft('legacy-new');
    expect(value).toMatchObject({publishKey:old().key, updatedAt:old().saved, draft:old().draft, undo:old().undo});
    expect(localStorage.getItem('notebook:draft:new')).toBeNull(); expect(migrateLegacyDraft()).toBe(true); expect(listDrafts()).toHaveLength(1);
  });
  it('retains both different payloads until explicit recovery', () => {
    localStorage.setItem('notebook:draft:new', JSON.stringify(old())); migrateLegacyDraft();
    const different = {...old(), draft:{...blank(), source_text:'Another'}};
    localStorage.setItem('notebook:draft:new', JSON.stringify(different));
    expect(migrateLegacyDraft()).toBe(false); expect(readDraft('legacy-new')?.draft.source_text).toBe('  Exact\n');
    expect(readLegacyDraft()?.draft.source_text).toBe('Another'); const recovered = recoverLegacyDraft();
    expect(recovered?.draft.source_text).toBe('Another'); expect(recovered?.id).not.toBe('legacy-new'); expect(listDrafts()).toHaveLength(2);
  });
  it('never destroys corrupt, quota-limited or failed-readback sources', () => {
    localStorage.setItem('notebook:draft:new', '{'); expect(migrateLegacyDraft()).toBe(false); expect(localStorage.getItem('notebook:draft:new')).toBe('{');
    const raw = JSON.stringify(old()); localStorage.setItem('notebook:draft:new', raw);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {});
    expect(migrateLegacyDraft()).toBe(false); expect(localStorage.getItem('notebook:draft:new')).toBe(raw);
  });
  it('keeps edit drafts revision-bound, validates optional bases, and preserves older records', () => {
    const valid = {...old(), revision:1, base:{revision:1, draft:{...blank(), title:'Original'}}};
    localStorage.setItem('notebook:draft:r1', JSON.stringify(valid));
    expect(readLegacyDraft('r1')?.base).toEqual(valid.base); expect(listDrafts()).toEqual([]);
    localStorage.setItem('notebook:draft:r1', JSON.stringify({...valid, base:{...valid.base, revision:2}}));
    expect(readLegacyDraft('r1')).toMatchObject({revision:1, draft:valid.draft});
    expect(readLegacyDraft('r1')?.base).toBeUndefined();
    localStorage.setItem('notebook:draft:r1', JSON.stringify({...old(), revision:1}));
    expect(readLegacyDraft('r1')?.base).toBeUndefined();
    localStorage.setItem('notebook:draft:r1', JSON.stringify(old())); expect(readLegacyDraft('r1')).toBeNull();
  });
});
