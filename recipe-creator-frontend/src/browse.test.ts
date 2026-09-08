import { describe, expect, it } from 'vitest';
import { bookmarkIds, browseUrl, mealGroup, uniqueTags } from './browse-query';

describe('browse query helpers', () => {
  it('preserves query and repeated selected tags in a saved URL', () => {
    expect(browseUrl({q: 'soup', tags: ['dinner', 'vegetarian'], saved: true})).toBe('/saved?q=soup&tag=dinner&tag=vegetarian');
  });

  it('uses the first configured meal classifier for legacy multi-classifier recipes', () => {
    expect(mealGroup(['Dessert', 'breakfast'], ['breakfast', 'lunch', 'dinner', 'dessert'])).toBe('breakfast');
    expect(mealGroup(['Italian'])).toBe('Other');
  });

  it('deduplicates selected tags and keeps bookmark order while rejecting malformed IDs', () => {
    expect(uniqueTags(['Dinner', 'dinner', 'vegetarian'])).toEqual(['Dinner', 'vegetarian']);
    expect(bookmarkIds(['recipes:one', 'one', 'two', '', 2, '../bad'])).toEqual({ids: ['one', 'two'], invalid: 3});
  });
});
