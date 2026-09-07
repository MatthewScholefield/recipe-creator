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
export function applyParse(draft: RecipeDraft, result: ParseResult): RecipeDraft { return {...draft, description: result.description, ingredient_groups: result.ingredient_groups, directions: result.directions, notes: result.notes, unclassified: result.unclassified, mode: 'structured', source_text: draft.source_text}; }
export class ParseGuard { private version = 0; private controller?: AbortController; start() { this.cancel(); return { version: this.version, signal: (this.controller = new AbortController()).signal }; } cancel() { this.version++; this.controller?.abort(); } accepts(version: number) { return this.version === version && !this.controller?.signal.aborted; } }
export function move<T>(items: T[], index: number, delta: number): T[] { const result = [...items]; const target = index + delta; if (target < 0 || target >= items.length) return result; [result[index], result[target]] = [result[target], result[index]]; return result; }
