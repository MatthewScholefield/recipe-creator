import { describe, expect, expectTypeOf, it } from 'vitest';
import type { components } from './api.generated';
import type { GramEstimate, Recipe, RecipeDraft } from './types';
import { blank, ingredient, quantity, scaled, ingredientText, formatRecipe, applyParse, ParseGuard, move } from './recipe';
import { load, save, safeUrl, translateLegacy } from './local';
describe('generated API contract', () => {
  it('links recipe fields and retains extensible server gram estimates in editor state', () => {
    type Schemas = components['schemas'];
    expectTypeOf<Omit<RecipeDraft, 'ingredient_groups'>>().toExtend<Omit<Schemas['RecipeDraft'], 'ingredient_groups'>>();
    expectTypeOf<Omit<Recipe, 'photos'>>().toExtend<Omit<Schemas['Recipe'], 'photos'>>();
    expectTypeOf<GramEstimate>().toEqualTypeOf<Schemas['GramEstimate']>();
    const grams: GramEstimate = {amount: 120, estimated: true, basis: 'Cup of flour', regional_assumption: 'US', grams_low: 110, grams_high: 130};
    const draft: RecipeDraft = {...blank('structured'), ingredient_groups: [{id: 'group', name: '', ingredients: [{...ingredient(), grams}]}]};
    expect(draft.ingredient_groups[0].ingredients[0].grams).toEqual(grams);
  });
});
describe('lossless recipe representations', () => {
  it('embeds exact prose and preserves unnamed and duplicate sections', () => {
    const draft = {...blank('structured'), description: '  A family favorite.\n', directions: 'Heat to 180°C.\n\n  Wait 20 minutes.\n', notes: '  Never change this.\n', unclassified: 'Mystery paragraph'};
    draft.ingredient_groups = ['', 'Sauce', 'Sauce'].map((name, i) => ({id: String(i), name, ingredients: [{...ingredient(), original_text: 'salt, to taste'}]}));
    const text = formatRecipe(draft);
    expect(text).toContain(draft.description); expect(text).toContain(draft.directions); expect(text).toContain(draft.notes); expect(text).toContain(draft.unclassified);
    expect(text.match(/=== Sauce ===/g)).toHaveLength(2); expect(text.match(/salt, to taste/g)).toHaveLength(3);
  });
  it('retains the original source and unknown regions on parse', () => {
    const draft = {...blank(), source_text: 'Original source\n  '};
    const result = applyParse(draft, {source_hash:'hash', description:'', ingredient_groups:[], directions:'exact', notes:' note ', unclassified:'unknown', warnings:[]});
    expect(result).not.toHaveProperty('source_hash'); expect(result).not.toHaveProperty('warnings'); expect(result.source_text).toBe(draft.source_text); expect(result.notes).toBe(' note '); expect(result.unclassified).toBe('unknown');
  });
  it('invalidates cancelled and superseded parses', () => {const guard = new ParseGuard(); const first = guard.start(); expect(guard.accepts(first.version)).toBe(true); const next = guard.start(); expect(first.signal.aborted).toBe(true); expect(guard.accepts(first.version)).toBe(false); expect(guard.accepts(next.version)).toBe(true); guard.cancel(); expect(guard.accepts(next.version)).toBe(false);});
  it('supports keyboard reorder without changing content', () => {expect(move(['a','b','c'], 1, -1)).toEqual(['b','a','c']); expect(move(['a'],0,-1)).toEqual(['a']);});
});
describe('safe ingredient quantities', () => {
  it.each([['½',0.5],['1½',1.5],['2 1/4',2.25],['0.25',0.25],['1/0',null],['to taste',null],['-2',null],['1–2',null]])('parses %s without guessing', (text, expected) => {expect(quantity(text as string)).toBe(expected);});
  it('scales fractions and ranges only in ingredients', () => {const row = {...ingredient(), quantity:'1/2', quantity_max:'1', unit:'cup', name:'flour'}; expect(ingredientText(row,2)).toBe('1–2 cup flour'); expect(scaled('to taste',2)).toBe('to taste');});
  it('labels estimates, retains unknown weights, and preserves originals', () => {const row = {...ingredient(), original_text:'one cup flour', quantity:'1', unit:'cup', name:'flour', grams:{amount:120, estimated:true, basis:'Estimated from a cup of flour'}}; expect(ingredientText(row,2,true)).toBe('≈ 240 g flour'); expect(row.original_text).toBe('one cup flour'); expect(ingredientText({...row, grams:null},2,true)).toBe('2 cup flour');});
});
describe('local preferences and legacy links', () => {
  it('remembers settings and tolerates corrupted storage', () => {save('grams', true); expect(load('grams',false)).toBe(true); localStorage.setItem('notebook:grams','bad'); expect(load('grams',false)).toBe(false);});
  it('translates legacy view/edit/tag routes safely', () => {expect(translateLegacy('#/view/a-b')).toBe('/recipes/a-b'); expect(translateLegacy('#/edit/a-b')).toBe('/recipes/a-b/edit'); expect(translateLegacy('#/tag/main%20dish')).toBe('/?q=tag%3Amain%20dish'); expect(translateLegacy('#/tag/%xx')).toBeNull(); expect(translateLegacy('#pair=private')).toBeNull();});
  it('rejects executable and relative source URLs', () => {expect(safeUrl('javascript:alert(1)')).toBeUndefined(); expect(safeUrl('/elsewhere')).toBeUndefined(); expect(safeUrl('https://example.com')).toBe('https://example.com/');});
});
