import { blank } from './recipe';
import { recipeDraftChangeSize } from './recipe-diff';
import type { RecipeDraft } from './types';

export interface DraftSummary { id: string; name: string; updatedAt: string }
export interface LocalDraft extends DraftSummary {
  version: 2;
  draft: RecipeDraft;
  publishKey: string;
  undo?: RecipeDraft;
  ingredientLines?: Record<string, string>;
}
export interface LegacyDraft {
  draft: RecipeDraft;
  revision?: number;
  key: string;
  undo?: RecipeDraft;
  saved: string;
  ingredientLines?: Record<string, string>;
  base?: {revision: number; draft: RecipeDraft};
}
const prefix = 'notebook:draft:v2:';
const legacyKey = 'notebook:draft:new';
const changed = 'notebook:drafts-changed';
export const EDIT_DRAFT_MAX_AGE_MS = 24 * 60 * 60 * 1000;
export const SIGNIFICANT_DRAFT_CHANGE_SIZE = 100;
const validId = (id: string) => /^(?:legacy-new|[a-f\d]{8}(?:-[a-f\d]{4}){3}-[a-f\d]{12})$/i.test(id);
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string';
const date = (value: unknown): value is string => text(value) && Number.isFinite(Date.parse(value));
const lines = (value: unknown) => value === undefined || (object(value) && Object.values(value).every(text));
const nameOf = (name = '') => name.trim().slice(0, 120) || 'Untitled recipe';

// Validate the entire editable shape before a stored value reaches keyed UI blocks.
export function isRecipeDraft(value: unknown): value is RecipeDraft {
  if (!object(value) || !['text', 'structured'].includes(String(value.mode))) return false;
  if (!['title', 'source_text', 'description', 'directions', 'notes', 'yield_unit', 'source_url', 'modifications'].every(key => text(value[key]))) return false;
  if (value.yield_amount !== null && !text(value.yield_amount)) return false;
  if (!Array.isArray(value.tags) || !value.tags.every(text) || !Array.isArray(value.ingredient_groups)) return false;
  const groupIds = new Set<string>(), rowIds = new Set<string>();
  return value.ingredient_groups.every(group => {
    if (!object(group) || !text(group.id) || !group.id || groupIds.has(group.id) || !text(group.name) || !Array.isArray(group.ingredients)) return false;
    groupIds.add(group.id);
    return group.ingredients.every(row => {
      if (!object(row) || !text(row.id) || !row.id || rowIds.has(row.id)) return false;
      rowIds.add(row.id);
      return ['original_text', 'unit', 'name', 'preparation'].every(key => text(row[key])) &&
        [row.quantity, row.quantity_max].every(value => value === null || text(value)) && typeof row.optional === 'boolean' &&
        (row.grams === null || object(row.grams));
    });
  });
}
function valid(value: unknown): value is LocalDraft {
  return object(value) && value.version === 2 && text(value.id) && validId(value.id) && text(value.name) &&
    !!value.name.trim() && value.name.length <= 120 && date(value.updatedAt) && text(value.publishKey) && !!value.publishKey &&
    isRecipeDraft(value.draft) && (value.undo === undefined || isRecipeDraft(value.undo)) && lines(value.ingredientLines);
}
function notify() { if (typeof window !== 'undefined') window.dispatchEvent(new Event(changed)); }
export function readDraft(id: string): LocalDraft | null {
  if (!validId(id)) return null;
  try { const value: unknown = JSON.parse(localStorage.getItem(prefix + id) || 'null'); return valid(value) && value.id === id ? value : null; } catch { return null; }
}
export function listDrafts(): DraftSummary[] {
  const result: DraftSummary[] = [];
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (!key?.startsWith(prefix)) continue;
      const value = readDraft(key.slice(prefix.length));
      if (value) result.push({id: value.id, name: value.name === 'Untitled recipe' ? nameOf(value.draft.title) : value.name, updatedAt: value.updatedAt});
    }
  } catch { /* Storage may be unavailable; in-memory editing still works. */ }
  return result.sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt) || a.id.localeCompare(b.id));
}
export function createDraft(name?: string, persist = true): LocalDraft {
  const value: LocalDraft = {version: 2, id: crypto.randomUUID(), name: nameOf(name), updatedAt: new Date().toISOString(), draft: blank('text'), publishKey: crypto.randomUUID()};
  if (persist) saveDraft(value);
  return value;
}
export function saveDraft(value: LocalDraft): boolean {
  if (!valid(value)) return false;
  try { localStorage.setItem(prefix + value.id, JSON.stringify(value)); notify(); return true; } catch { return false; }
}
export function renameDraft(id: string, name: string): boolean {
  const value = readDraft(id);
  return !!value && saveDraft({...value, name: nameOf(name), updatedAt: new Date().toISOString()});
}
export function deleteDraft(id: string): boolean {
  if (!validId(id)) return false;
  try { localStorage.removeItem(prefix + id); notify(); return true; } catch { return false; }
}
export function deleteLegacyDraft(recipeId?: string): boolean {
  const key = recipeId ? `notebook:draft:${recipeId}` : legacyKey;
  try { localStorage.removeItem(key); notify(); return localStorage.getItem(key) === null; } catch { return false; }
}
export function draftHref(id: string): string { return `/new?draft=${encodeURIComponent(id)}`; }
export function subscribeDrafts(listener: () => void): () => void {
  const storage = (event: StorageEvent) => { if (event.key === null || event.key.startsWith(prefix) || event.key === legacyKey) listener(); };
  window.addEventListener(changed, listener);
  window.addEventListener('storage', storage);
  return () => { window.removeEventListener(changed, listener); window.removeEventListener('storage', storage); };
}
export function readLegacyDraft(recipeId?: string): LegacyDraft | null {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(recipeId ? `notebook:draft:${recipeId}` : legacyKey) || 'null');
    if (!object(value) || !isRecipeDraft(value.draft) || !text(value.key) || !value.key || !date(value.saved) ||
      (value.undo !== undefined && !isRecipeDraft(value.undo)) || !lines(value.ingredientLines) ||
      (recipeId && (!Number.isInteger(value.revision) || Number(value.revision) < 1))) return null;
    const {base: storedBase, ...draft} = value;
    const base = object(storedBase) && Number.isInteger(storedBase.revision) && Number(storedBase.revision) >= 1 &&
      storedBase.revision === value.revision && isRecipeDraft(storedBase.draft)
      ? {revision: Number(storedBase.revision), draft: storedBase.draft}
      : undefined;
    return {...draft, ...(base ? {base} : {})} as unknown as LegacyDraft;
  } catch { return null; }
}
export function readEditDraft(recipeId: string, current: {revision: number; draft: RecipeDraft}, now = Date.now()): LegacyDraft | null {
  const value = readLegacyDraft(recipeId);
  if (!value) return null;
  const original = value.base?.draft ?? (value.revision === current.revision ? current.draft : undefined);
  if (original && recipeDraftChangeSize(original, value.draft) === 0) {
    deleteLegacyDraft(recipeId);
    return null;
  }
  if (now - Date.parse(value.saved) <= EDIT_DRAFT_MAX_AGE_MS) return value;
  if (!original || recipeDraftChangeSize(original, value.draft) >= SIGNIFICANT_DRAFT_CHANGE_SIZE) return value;
  return deleteLegacyDraft(recipeId) ? null : value;
}
function migrated(value: LegacyDraft, id: string): LocalDraft {
  return {version: 2, id, name: nameOf(value.draft.title.trim() ? value.draft.title : 'Recovered recipe'), updatedAt: value.saved,
    draft: value.draft, publishKey: value.key, ...(value.undo ? {undo: value.undo} : {}),
    ...(value.ingredientLines ? {ingredientLines: value.ingredientLines} : {})};
}
export function migrateLegacyDraft(): boolean {
  try {
    const raw = localStorage.getItem(legacyKey);
    if (raw === null) return true;
    const old = readLegacyDraft();
    if (!old) return false;
    const value = migrated(old, 'legacy-new');
    const destination = localStorage.getItem(prefix + value.id);
    if (destination !== null && JSON.stringify(readDraft(value.id)) !== JSON.stringify(value)) return false;
    if (destination === null && !saveDraft(value)) return false;
    if (JSON.stringify(readDraft(value.id)) !== JSON.stringify(value) || localStorage.getItem(legacyKey) !== raw) return false;
    localStorage.removeItem(legacyKey);
    notify();
    return true;
  } catch { return false; }
}
// Explicit recovery of a conflicting old slot never changes the existing v2 destination.
export function recoverLegacyDraft(): LocalDraft | null {
  const old = readLegacyDraft();
  if (!old) return null;
  const value = migrated(old, crypto.randomUUID());
  if (!saveDraft(value) || JSON.stringify(readDraft(value.id)) !== JSON.stringify(value)) return null;
  try { if (JSON.stringify(readLegacyDraft()) === JSON.stringify(old)) localStorage.removeItem(legacyKey); } catch { /* Both copies are safe. */ }
  notify();
  return value;
}
