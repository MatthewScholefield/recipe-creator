import type { Ingredient, RecipeDraft, ParseResult } from './types';
export function blank(mode: RecipeDraft['mode'] = 'text'): RecipeDraft { return { title: '', source_text: '', mode, description: '', ingredient_groups: [], directions: '', notes: '', unclassified: '', tags: [], yield_amount: null, yield_unit: '', source_url: '', modifications: '' }; }
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
  return [draft.description, draft.ingredient_groups.length ? 'Ingredients\n' + draft.ingredient_groups.map(group => [group.name ? `=== ${group.name} ===` : '', ...group.ingredients.map(row => ingredientText(row))].filter(Boolean).join('\n')).join('\n\n') : '', draft.directions ? `Directions\n${draft.directions}` : '', draft.notes ? `Notes\n${draft.notes}` : '', draft.unclassified || ''].filter(Boolean).join('\n\n');
}
export function applyParse(draft: RecipeDraft, result: ParseResult): RecipeDraft {
  // Reorganizing retained text must not replace separately authored fields or legacy IDs.
  const oldRows = draft.ingredient_groups.flatMap(group => group.ingredients);
  const used = new Set<string>(), usedGroups = new Set<string>();
  const groups = result.ingredient_groups.map(group => {
    const ingredients = group.ingredients.map(output => {
      const row: Ingredient = {...output, quantity: output.quantity ?? null, quantity_max: output.quantity_max ?? null, grams: output.grams ?? null};
      const old = oldRows.find(candidate => !used.has(candidate.id) && ingredientLine(candidate) === ingredientLine(row));
      if (old) { used.add(old.id); return old; }
      return row;
    });
    const old = draft.ingredient_groups.find(candidate => !usedGroups.has(candidate.id) && (candidate.id === group.id ||
      (candidate.name === group.name && candidate.ingredients.some(row => ingredients.some(item => item.id === row.id)))));
    if (old) usedGroups.add(old.id);
    return {...group, id: old?.id ?? group.id, ingredients};
  });
  // An organizer classifies source; it cannot silently delete separately authored legacy rows.
  for (const old of draft.ingredient_groups) {
    const remaining = old.ingredients.filter(row => !used.has(row.id));
    const group = groups.find(group => group.id === old.id);
    if (group) group.ingredients.push(...remaining);
    else if (remaining.length || !old.ingredients.length) groups.push({...old, ingredients: remaining});
  }
  return {...draft, description: draft.description || result.description, ingredient_groups: groups,
    directions: draft.directions || result.directions, notes: draft.notes || result.notes,
    unclassified: draft.unclassified || result.unclassified, mode: 'structured', source_text: draft.source_text};
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
export function applyIngredientLines(draft: RecipeDraft, snapshot: IngredientLineSnapshot,
  items: {id: string; text: string; method: string; ingredient: Ingredient}[]): RecipeDraft {
  if (items.length !== snapshot.length || new Set(items.map(item => item.id)).size !== snapshot.length ||
    items.some((item, index) => item.id !== snapshot[index].id || item.text !== snapshot[index].text ||
      item.ingredient.id !== item.id || item.ingredient.original_text !== item.text || item.ingredient.grams !== null ||
      !['deterministic', 'llm', 'unparsed'].includes(item.method))) throw new Error('Ingredient preview did not match your text. Result ignored.');
  const byId = new Map(items.map(item => [item.id, item]));
  return {...draft, ingredient_groups: draft.ingredient_groups.map(group => ({...group, ingredients: group.ingredients.map(row => {
    const item = byId.get(row.id);
    if (!item) return row;
    if (ingredientLine(row) !== item.text) throw new Error('Ingredient changed while organizing. Result ignored.');
    return item.method === 'unparsed' ? changedIngredient(row, item.text) : {...item.ingredient};
  })}))};
}
export function withoutEmptyIngredients(draft: RecipeDraft): RecipeDraft {
  return {...draft, ingredient_groups: draft.ingredient_groups.map(group => ({...group,
    ingredients: group.ingredients.filter(row => ingredientLine(row).trim())}))};
}
export class ParseGuard { private version = 0; private controller?: AbortController; start() { this.cancel(); return { version: this.version, signal: (this.controller = new AbortController()).signal }; } cancel() { this.version++; this.controller?.abort(); } accepts(version: number) { return this.version === version && !this.controller?.signal.aborted; } }
export function move<T>(items: T[], index: number, delta: number): T[] { const result = [...items]; const target = index + delta; if (target < 0 || target >= items.length) return result; [result[index], result[target]] = [result[target], result[index]]; return result; }
