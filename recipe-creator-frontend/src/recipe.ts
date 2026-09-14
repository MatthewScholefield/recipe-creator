import type { Ingredient, Recipe, RecipeDraft, ParseResult } from './types';
export function blank(mode: RecipeDraft['mode'] = 'text'): RecipeDraft { return { title: '', source_text: '', mode, description: '', ingredient_groups: [], directions: '', notes: '', tags: [], yield_amount: null, yield_unit: '', source_url: '', modifications: '' }; }
export function ingredient(): Ingredient { return { id: crypto.randomUUID(), original_text: '', quantity: null, quantity_max: null, unit: '', name: '', preparation: '', optional: false, grams: null }; }
export function recipeDraftFromRecipe(recipe: Recipe): RecipeDraft {
  const empty = blank();
  return JSON.parse(JSON.stringify(Object.fromEntries(
    Object.keys(empty).map(key => [key, recipe[key as keyof Recipe] ?? empty[key as keyof RecipeDraft]]),
  ))) as RecipeDraft;
}
const fractions: Record<string, string> = {
  '¼': '1/4', '½': '1/2', '¾': '3/4', '⅐': '1/7', '⅑': '1/9',
  '⅒': '1/10', '⅓': '1/3', '⅔': '2/3', '⅕': '1/5', '⅖': '2/5',
  '⅗': '3/5', '⅘': '4/5', '⅙': '1/6', '⅚': '5/6', '⅛': '1/8',
  '⅜': '3/8', '⅝': '5/8', '⅞': '7/8',
};
const fractionGlyphPattern = /[¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞]/g;
export function quantity(value: string | null | undefined): number | null {
  if (!value) return null;
  const text = value.trim()
    .replace('⁄', '/')
    .replace(/(\d)([¼½¾⅐⅑⅒⅓⅔⅕⅖⅗⅘⅙⅚⅛⅜⅝⅞])/g, '$1 $2')
    .replace(fractionGlyphPattern, glyph => fractions[glyph])
    .replace(/\s*\/\s*/g, '/');
  let result: number | null = null;
  if (/^(?:\d+(?:\.\d+)?|\.\d+)$/.test(text)) result = Number(text);
  const match = /^(?:(\d+)\s+)?(\d+)\/(\d+)$/.exec(text);
  if (match && Number(match[3]) !== 0) {
    result = Number(match[1] || 0) + Number(match[2]) / Number(match[3]);
  }
  return result !== null && Number.isFinite(result) ? result : null;
}

type FractionPart = readonly [numerator: number, denominator: number, glyph: string];
type UnitPolicy = {
  display: 'fractions' | 'decimal';
  fractions: readonly FractionPart[];
};
const eighthFractions: readonly FractionPart[] = [
  [1, 8, '⅛'], [1, 4, '¼'], [1, 3, '⅓'], [3, 8, '⅜'], [1, 2, '½'],
  [5, 8, '⅝'], [2, 3, '⅔'], [3, 4, '¾'], [7, 8, '⅞'],
];
const quarterThirdFractions: readonly FractionPart[] = [
  [1, 4, '¼'], [1, 3, '⅓'], [1, 2, '½'], [2, 3, '⅔'], [3, 4, '¾'],
];
const quarterFractions: readonly FractionPart[] = [
  [1, 4, '¼'], [1, 2, '½'], [3, 4, '¾'],
];
const unitPolicies: Record<string, UnitPolicy> = {
  cup: {display: 'fractions', fractions: eighthFractions},
  tbsp: {display: 'fractions', fractions: quarterThirdFractions},
  tsp: {display: 'fractions', fractions: eighthFractions},
  oz: {display: 'fractions', fractions: quarterFractions},
  lb: {display: 'fractions', fractions: quarterFractions},
  'fl oz': {display: 'fractions', fractions: quarterFractions},
  g: {display: 'decimal', fractions: []},
  kg: {display: 'decimal', fractions: []},
  mg: {display: 'decimal', fractions: []},
  ml: {display: 'decimal', fractions: []},
  l: {display: 'decimal', fractions: []},
};
const unitAliases: Record<string, string> = {
  cup: 'cup', cups: 'cup',
  tbsp: 'tbsp', tablespoon: 'tbsp', tablespoons: 'tbsp',
  tsp: 'tsp', teaspoon: 'tsp', teaspoons: 'tsp',
  oz: 'oz', ounce: 'oz', ounces: 'oz',
  lb: 'lb', lbs: 'lb', pound: 'lb', pounds: 'lb',
  'fl oz': 'fl oz', 'fl. oz': 'fl oz',
  'fluid ounce': 'fl oz', 'fluid ounces': 'fl oz',
  g: 'g', kg: 'kg', mg: 'mg', ml: 'ml', l: 'l',
};
const decimalPolicy: UnitPolicy = {display: 'decimal', fractions: []};
function unitPolicy(unit: string): UnitPolicy {
  const lookup = unit.trim().replace(/\s+/g, ' ').toLowerCase().replace(/\.+$/, '');
  return unitPolicies[unitAliases[lookup]] || decimalPolicy;
}
const quantityFormatter = new Intl.NumberFormat('en', {maximumFractionDigits: 3});
export function scaled(value: string | null, scale: number): string {
  const number = quantity(value);
  return number === null || !Number.isFinite(scale) || scale <= 0
    ? value || ''
    : quantityFormatter.format(number * scale);
}
function formattedQuantity(value: string | null, unit: string, scale: number): string {
  const number = quantity(value);
  if (number === null || !Number.isFinite(scale) || scale <= 0) return scaled(value, scale);
  const scaledNumber = number * scale;
  const policy = unitPolicy(unit);
  if (
    policy.display === 'fractions'
    && Number.isFinite(scaledNumber)
    && Math.abs(scaledNumber) < Number.MAX_SAFE_INTEGER
  ) {
    if (Number.isInteger(scaledNumber)) return quantityFormatter.format(scaledNumber);
    const whole = Math.floor(scaledNumber);
    const fractional = scaledNumber - whole;
    const tolerance = 4 * Number.EPSILON * Math.max(1, Math.abs(scaledNumber));
    const match = policy.fractions.find(
      ([numerator, denominator]) => Math.abs(fractional - numerator / denominator) <= tolerance,
    );
    if (match) return whole ? `${whole} ${match[2]}` : match[2];
  }
  return quantityFormatter.format(scaledNumber);
}
export function ingredientAmount(row: Ingredient, scale = 1): string {
  const range = row.quantity_max
    ? `–${formattedQuantity(row.quantity_max, row.unit, scale)}`
    : '';
  return [
    formattedQuantity(row.quantity, row.unit, scale) + range,
    row.unit,
  ].filter(Boolean).join(' ');
}
const gramFormatter = new Intl.NumberFormat('en', {maximumFractionDigits: 1});
function gramNumber(value: unknown): number | null {
  if ((typeof value !== 'number' && typeof value !== 'string') || quantity(String(value)) === null) return null;
  return Number(value);
}
export function gramValues(row: Ingredient, scale = 1): {amount: number; uncertainty: number | null} | null {
  const explicit = gramNumber(row.grams?.amount);
  const low = gramNumber(row.grams?.low), high = gramNumber(row.grams?.high);
  const amount = explicit ?? (low !== null && high !== null ? (low + high) / 2 : null);
  if (amount === null) return null;
  const uncertainty = low !== null && high !== null && high > low ? (high - low) / 2 * scale : null;
  return {amount: amount * scale, uncertainty};
}
export function gramText(row: Ingredient, scale = 1): string | null {
  const values = gramValues(row, scale);
  return values ? `${gramFormatter.format(values.amount)} g` : null;
}
export function gramEstimateText(row: Ingredient, scale = 1): string | null {
  const values = gramValues(row, scale);
  if (!values) return null;
  const uncertainty = values.uncertainty === null ? '' : ` ± ${gramFormatter.format(values.uncertainty)}`;
  return `${gramFormatter.format(values.amount)}${uncertainty} g`;
}
export function ingredientText(row: Ingredient, scale = 1, grams = false): string {
  const weight = gramText(row, scale);
  if (grams && weight) return `${row.grams?.estimated === false ? '' : '≈ '}${weight} ${row.name || row.original_text}${row.preparation ? `, ${row.preparation}` : ''}${row.optional ? ' (optional)' : ''}`;
  if (!row.name) return row.original_text;
  return [ingredientAmount(row, scale), row.name].filter(Boolean).join(' ') + (row.preparation ? `, ${row.preparation}` : '') + (row.optional ? ' (optional)' : '');
}
export function formatRecipe(draft: RecipeDraft): string {
  const sections: string[] = [];
  const prose = (title: string, value: string) => {
    const text = value.trim();
    if (text) sections.push(`## ${title}\n\n${text}`);
  };
  prose('Description', draft.description);
  if (draft.ingredient_groups.length) {
    const groups = draft.ingredient_groups.map(group => {
      const name = group.name.trim().replace(/\s+/g, ' ');
      const lines = group.ingredients.map(row => ingredientText(row).trim()).filter(Boolean).map(line => `- ${line}`);
      return [name && draft.ingredient_groups.length > 1 ? `### ${name}` : '', ...lines].filter(Boolean).join('\n');
    }).filter(Boolean);
    if (groups.length) sections.push(`## Ingredients\n\n${groups.join('\n\n')}`);
  }
  prose('Directions', draft.directions);
  prose('Notes', draft.notes);
  return sections.join('\n\n');
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
export function tagInput(value: string): string {
  return value.normalize('NFC').toLocaleLowerCase()
    .replace(/ß/g, 'ss').replace(/ſ/g, 's').replace(/ς/g, 'σ')
    .replace(/[^\p{L}\p{N}]+/gu, '-');
}
export function canonicalTag(value: string): string {
  return tagInput(value).replace(/^-+|-+$/g, '');
}
export function normalizeTags(tags: string[]): string[] {
  const seen = new Set<string>();
  return tags.map(canonicalTag).filter(tag => {
    if (!tag || seen.has(tag)) return false;
    seen.add(tag); return true;
  });
}
export function tagError(tags: string[]): string {
  const normalized = normalizeTags(tags);
  if (normalized.filter(tag => MEAL_CLASSIFIERS.includes(tag as typeof MEAL_CLASSIFIERS[number])).length > 1) return classifierMessage;
  if (normalized.length > 50 || tags.some(tag => tag !== canonicalTag(tag) || [...tag].length > 80)) {
    return 'Use up to 50 lowercase kebab-case tags, each 1–80 letters or numbers.';
  }
  return '';
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
