import { beforeEach, describe, expect, it, vi } from 'vitest';
import { blank } from './recipe';
import { createDraft, deleteDraft, draftHref, listDrafts, migrateLegacyDraft, readDraft, readLegacyDraft, recoverLegacyDraft, renameDraft, saveDraft, subscribeDrafts } from './drafts';

beforeEach(() => { localStorage.clear(); vi.restoreAllMocks(); });
describe('independent local drafts', () => {
  it('creates separate names and keys, lists, renames and deletes only the target', () => {
    const a = createDraft('  Soup  '), b = createDraft('Soup');
    expect(a.id).not.toBe(b.id); expect(a.publishKey).not.toBe(a.id); expect(a.publishKey).not.toBe(b.publishKey);
    expect(listDrafts()).toHaveLength(2); expect(renameDraft(a.id, ' Stew ')).toBe(true);
    expect(readDraft(a.id)?.name).toBe('Stew'); expect(readDraft(b.id)?.name).toBe('Soup');
    expect(draftHref(b.id)).toBe(`/new?draft=${b.id}`); expect(deleteDraft(a.id)).toBe(true);
    expect(readDraft(b.id)?.publishKey).toBe(b.publishKey);
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
    const a = createDraft('Safe'); expect(a.draft.mode).toBe('text'); expect(saveDraft(a)).toBe(false); expect(a.name).toBe('Safe');
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
