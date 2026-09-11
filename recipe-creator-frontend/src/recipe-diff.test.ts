import { describe, expect, it } from 'vitest';
import { blank, ingredient } from './recipe';
import { recipeDiffSections, recipeTextDiff } from './recipe-diff';
import type { RecipeDraft } from './types';

const section = (draft: RecipeDraft, key: keyof RecipeDraft) =>
  recipeDiffSections(draft).find(item => item.key === key)!.text;
const rendered = (oldText: string, newText: string) => recipeTextDiff(oldText, newText).flatMap(hunk => hunk.lines.map(line => line.text));

describe('recipe conflict diff projection', () => {
  it('attributes each side to the common base and keeps saved-only fields on the saved side', () => {
    const base = {...blank('structured'), directions: 'Simmer 20 minutes', notes: ''};
    const candidate = {...base, directions: 'Simmer 30 minutes'};
    const latest = {...base, directions: 'Simmer 25 minutes', notes: 'Serve hot'};

    expect(rendered(section(base, 'directions'), section(candidate, 'directions'))).toEqual(expect.arrayContaining([
      '-Simmer 20 minutes', '+Simmer 30 minutes',
    ]));
    expect(rendered(section(base, 'directions'), section(latest, 'directions'))).toEqual(expect.arrayContaining([
      '-Simmer 20 minutes', '+Simmer 25 minutes',
    ]));
    expect(recipeTextDiff(section(base, 'notes'), section(candidate, 'notes'))).toEqual([]);
    expect(rendered(section(base, 'notes'), section(latest, 'notes'))).toContain('+Serve hot');
  });

  it('exposes parsed ingredient edits, group renames, exact whitespace, and omits derived fields', () => {
    const row = {...ingredient(), id: 'internal-row', original_text: '1 cup flour', quantity: '1', name: 'flour', grams: {amount: 120, estimated: true, basis: 'test'}};
    const base = {...blank('structured'), description: 'First\n\nSecond', ingredient_groups: [{id: 'internal-group', name: 'Dough', ingredients: [row]}]};
    const changed = {...base, description: 'First\n \nSecond', ingredient_groups: [{...base.ingredient_groups[0], name: 'Main dough', ingredients: [{...row, quantity: '2', name: 'bread flour'}]}]};
    const originalIngredients = section(base, 'ingredient_groups');
    const changedIngredients = section(changed, 'ingredient_groups');

    expect(originalIngredients).toContain('Section: "Dough"');
    expect(changedIngredients).toContain('Section: "Main dough"');
    expect(changedIngredients).toContain('"quantity":"2"');
    expect(changedIngredients).toContain('"name":"bread flour"');
    expect(changedIngredients).not.toContain('internal-row');
    expect(changedIngredients).not.toContain('internal-group');
    expect(changedIngredients).not.toContain('grams');
    expect(recipeTextDiff(section(base, 'description'), section(changed, 'description'))).not.toEqual([]);
  });

  it('distinguishes null and empty yield amounts and preserves missing final newlines', () => {
    const empty = blank();
    const emptyString = {...empty, yield_amount: ''};
    expect(section(empty, 'yield_amount')).toBe('null');
    expect(section(emptyString, 'yield_amount')).toBe('""');
    expect(rendered('line\n', 'line')).toContain('\\ No newline at end of file');
  });
});
