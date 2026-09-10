import type { Ingredient, RecipeDraft, ParseResult } from './types';
export function blank(mode: RecipeDraft['mode'] = 'text'): RecipeDraft { return { title: '', source_text: '', mode, description: '', ingredient_groups: [], directions: '', notes: '', tags: [], yield_amount: null, yield_unit: '', source_url: '', modifications: '' }; }
export function ingredient(): Ingredient { return { id: crypto.randomUUID(), original_text: '', quantity: null, quantity_max: null, unit: '', name: '', preparation: '', optional: false, grams: null }; }
const fractions: Record<string, string> = { '½':'1/2','¼':'1/4','¾':'3/4','⅓':'1/3','⅔':'2/3','⅛':'1/8','⅜':'3/8','⅝':'5/8','⅞':'7/8' };
export function quantity(value: string | null | undefined): number | null {
  if (!value) return null;
  let text = value.trim().replace(/(\d)([½¼¾⅓⅔⅛⅜⅝⅞])/g, '$1 $2').replace(/[½¼¾⅓⅔⅛⅜⅝⅞]/g, ch => fractions[ch]);
  if (/^\d+(?:\.\d+)?$/.test(text)) return Number(text);
  const m = /^(?:(\d+)\s+)?(\d+)\/(\d+)$/.exec(text);
  return m && Number(m[3]) !== 0 ? Number(m[1] || 0) + Number(m[2]) / Number(m[3]) : null;
}
export function scaled(value: string | null, scale: number): string { const number = quantity(value); return number === null || !Number.isFinite(scale) || scale <= 0 ? value || '' : new Intl.NumberFormat('en', {maximumFractionDigits: 3}).format(number * scale); }
export function ingredientText(row: Ingredient, scale = 1, grams = false): string {
  const amount = row.grams?.amount;
  if (grams && amount != null && quantity(String(amount)) !== null) return `${row.grams?.estimated === false ? '' : '≈ '}${scaled(String(amount), scale)} g ${row.name || row.original_text}${row.preparation ? `, ${row.preparation}` : ''}${row.optional ? ' (optional)' : ''}`;
  if (!row.name) return row.original_text;
  const range = row.quantity_max ? `–${scaled(row.quantity_max, scale)}` : '';
  return [scaled(row.quantity, scale) + range, row.unit, row.name].filter(Boolean).join(' ') + (row.preparation ? `, ${row.preparation}` : '') + (row.optional ? ' (optional)' : '');
}
export function formatRecipe(draft: RecipeDraft): string {
  return [draft.description, draft.ingredient_groups.length ? 'Ingredients\n' + draft.ingredient_groups.map(group => [group.name && draft.ingredient_groups.length > 1 ? `=== ${group.name} ===` : '', ...group.ingredients.map(row => ingredientText(row))].filter(Boolean).join('\n')).join('\n\n') : '', draft.directions ? `Directions\n${draft.directions}` : '', draft.notes ? `Notes\n${draft.notes}` : ''].filter(Boolean).join('\n\n');
}
export function applyParse(draft: RecipeDraft, result: ParseResult): RecipeDraft {
  return {
    ...draft,
    ...result,
    ingredient_groups: result.ingredient_groups.map(group => ({
      ...group,
      ingredients: group.ingredients.map(row => ({
        ...row,
        quantity: row.quantity ?? null,
        quantity_max: row.quantity_max ?? null,
        grams: row.grams ?? null,
      })),
    })),
    mode: 'structured',
    source_text: draft.source_text,
  };
}
export const MEAL_CLASSIFIERS = ['breakfast', 'lunch', 'dinner', 'dessert'] as const;
export const classifierMessage = 'Choose only one meal type: breakfast, lunch, dinner or dessert.';
const tagKey = (value: string) => value.trim().normalize('NFC').toLowerCase().replace(/ß/g, 'ss').replace(/ſ/g, 's').replace(/ς/g, 'σ');
export function normalizeTags(tags: string[]): string[] {
  const seen = new Set<string>();
  return tags.map(tag => tag.trim().normalize('NFC')).filter(tag => {
    const key = tagKey(tag); if (seen.has(key)) return false; seen.add(key); return true;
  }).map(tag => MEAL_CLASSIFIERS.some(meal => meal === tagKey(tag)) ? tagKey(tag) : tag);
}
export function tagError(tags: string[]): string {
  const normalized = normalizeTags(tags);
  if (normalized.filter(tag => MEAL_CLASSIFIERS.some(meal => meal === tagKey(tag))).length > 1) return classifierMessage;
  return normalized.length > 50 || normalized.some(tag => !tag || [...tag].length > 80) ? 'Choose up to 50 tags, each 1–80 characters.' : '';
}
export function ingredientLine(row: Ingredient): string { return row.original_text || ingredientText(row); }
export function changedIngredient(row: Ingredient, text: string): Ingredient {
  return {...row, original_text: text, quantity: null, quantity_max: null, grams: null, unit: '', name: '', preparation: '', optional: false};
}
export type IngredientLineSnapshot = {id: string; text: string}[];
export function dirtyIngredientLines(draft: RecipeDraft, dirty: Record<string, string>): IngredientLineSnapshot {
  return draft.ingredient_groups.flatMap(group => group.ingredients)
    .filter(row => Object.hasOwn(dirty, row.id) && dirty[row.id].trim())
    .map(row => ({id: row.id, text: dirty[row.id]}));
}
export function applyIngredientLines(draft: RecipeDraft,
  items: {id: string; ingredient: Ingredient}[]): RecipeDraft {
  const byId = new Map(items.map(item => [item.id, item.ingredient]));
  return {...draft, ingredient_groups: draft.ingredient_groups.map(group => ({...group, ingredients: group.ingredients.map(row =>
    byId.get(row.id) ?? row
  )}))};
}
export function withoutEmptyIngredients(draft: RecipeDraft): RecipeDraft {
  return {...draft, ingredient_groups: draft.ingredient_groups.map(group => ({...group,
    ingredients: group.ingredients.filter(row => ingredientLine(row).trim())}))};
}
export class ParseGuard { private version = 0; private controller?: AbortController; start() { this.cancel(); return { version: this.version, signal: (this.controller = new AbortController()).signal }; } cancel() { this.version++; this.controller?.abort(); } accepts(version: number) { return this.version === version && !this.controller?.signal.aborted; } }
export function move<T>(items: T[], index: number, delta: number): T[] { const result = [...items]; const target = index + delta; if (target < 0 || target >= items.length) return result; [result[index], result[target]] = [result[target], result[index]]; return result; }
