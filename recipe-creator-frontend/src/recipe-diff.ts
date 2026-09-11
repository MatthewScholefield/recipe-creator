import { structuredPatch } from 'diff';
import type { StructuredPatchHunk } from 'diff';
import type { RecipeDraft } from './types';

export type RecipeDiffSection = {key: keyof RecipeDraft; label: string; text: string};
export type RecipeDiffHunk = {range: string; lines: {kind: 'context' | 'addition' | 'removal' | 'marker'; text: string}[]};

const fields: {key: keyof RecipeDraft; label: string}[] = [
  {key: 'title', label: 'Title'},
  {key: 'mode', label: 'Format'},
  {key: 'description', label: 'Description'},
  {key: 'ingredient_groups', label: 'Ingredients'},
  {key: 'directions', label: 'Directions'},
  {key: 'notes', label: 'Notes'},
  {key: 'tags', label: 'Tags'},
  {key: 'yield_amount', label: 'Yield amount'},
  {key: 'yield_unit', label: 'Yield unit'},
  {key: 'source_url', label: 'Source link'},
  {key: 'modifications', label: 'Modifications'},
  {key: 'source_text', label: 'Original source text'},
];

function ingredientText(draft: RecipeDraft): string {
  return draft.ingredient_groups.flatMap(group => [
    `Section: ${JSON.stringify(group.name)}`,
    ...group.ingredients.map(row => JSON.stringify({
      original_text: row.original_text,
      quantity: row.quantity,
      quantity_max: row.quantity_max,
      unit: row.unit,
      name: row.name,
      preparation: row.preparation,
      optional: row.optional,
    })),
  ]).join('\n');
}

export function recipeDiffSections(draft: RecipeDraft): RecipeDiffSection[] {
  return fields.map(({key, label}) => {
    let text: string;
    if (key === 'ingredient_groups') text = ingredientText(draft);
    else if (key === 'tags') text = draft.tags.map(tag => JSON.stringify(tag)).join('\n');
    else if (key === 'yield_amount') text = JSON.stringify(draft.yield_amount);
    else text = String(draft[key]);
    return {key, label, text};
  });
}

function lineKind(line: string): RecipeDiffHunk['lines'][number]['kind'] {
  if (line.startsWith('+')) return 'addition';
  if (line.startsWith('-')) return 'removal';
  if (line.startsWith('\\')) return 'marker';
  return 'context';
}

function range(hunk: StructuredPatchHunk): string {
  return `@@ -${hunk.oldStart},${hunk.oldLines} +${hunk.newStart},${hunk.newLines} @@`;
}

export function recipeTextDiff(original: string, changed: string): RecipeDiffHunk[] {
  if (original === changed) return [];
  const patch = structuredPatch('original', 'changed', original, changed, undefined, undefined, {
    context: 3,
    ignoreWhitespace: false,
    stripTrailingCr: false,
  });
  return patch.hunks.map(hunk => ({
    range: range(hunk),
    lines: hunk.lines.map(line => ({kind: lineKind(line), text: line})),
  }));
}
