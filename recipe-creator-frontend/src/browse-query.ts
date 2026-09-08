export const MEAL_CLASSIFIERS = ['breakfast', 'lunch', 'dinner', 'dessert'] as const;

function tagKey(value: string): string {
  return value.trim().normalize('NFC').toLocaleLowerCase();
}

export function browseUrl({q, tags, saved}: {q: string; tags: string[]; saved: boolean}): string {
  const params = new URLSearchParams();
  if (q) params.set('q', q);
  for (const tag of tags) params.append('tag', tag);
  const query = params.toString();
  return `${saved ? '/saved' : '/'}${query ? `?${query}` : ''}`;
}

export function mealGroup(tags: string[], classifiers: readonly string[] = MEAL_CLASSIFIERS): string {
  const keys = new Set(tags.map(tagKey));
  return classifiers.find(classifier => keys.has(tagKey(classifier))) || 'Other';
}

export function uniqueTags(tags: string[]): string[] {
  const seen = new Set<string>();
  return tags.filter(tag => {
    const key = tagKey(tag);
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function bookmarkIds(values: unknown): {ids: string[]; invalid: number} {
  if (!Array.isArray(values)) return {ids: [], invalid: 0};
  const ids: string[] = [];
  const seen = new Set<string>();
  let invalid = 0;
  for (const value of values) {
    if (typeof value !== 'string') { invalid++; continue; }
    const id = value.startsWith('recipes:') ? value.slice('recipes:'.length) : value;
    if (!/^[A-Za-z0-9_-]{1,160}$/.test(id)) { invalid++; continue; }
    if (seen.has(id)) continue;
    seen.add(id);
    ids.push(id);
  }
  return {ids, invalid};
}
