import type { components } from './api.generated';

type Schemas = components['schemas'];

export type User = Schemas['PublicUser'];
export type Session = Schemas['SessionResponse'];
export type GramEstimate = Schemas['GramEstimate'];
// Editor state keeps output-only estimates; the write API discards them.
export type Ingredient = Omit<Required<Schemas['Ingredient']>, 'grams'> & { grams: GramEstimate | null };
export type IngredientGroup = Omit<Required<Schemas['IngredientGroup']>, 'ingredients'> & { ingredients: Ingredient[] };
export type RecipeDraft = Omit<Required<Schemas['RecipeDraft']>, 'ingredient_groups'> & { ingredient_groups: IngredientGroup[] };
export interface Photo { id: string; recipe_id: string; uploader_id?: string; uploader_name?: string; caption: string; state: string; can_delete?: boolean }
export interface AdminPhoto extends Omit<Photo, 'state' | 'can_delete'> { status: 'pending' | 'approved' | 'rejected' }
export type Recipe = Omit<Schemas['Recipe'], 'ingredient_groups' | 'photos'> & { ingredient_groups: IngredientGroup[]; photos?: Photo[] };
export type RecipeSummary = Schemas['RecipeSummary'];
export type ParseResult = Schemas['ParseResult'];
export type SiteCopy = Schemas['SiteCopy'];
export type SiteSettings = Schemas['SiteSettings'];
export type TagCatalog = Schemas['TagCatalog'];
export type RecipeListResponse = Schemas['RecipeListResponse'];
export type IngredientLinesRequest = Schemas['IngredientLinesRequest'];
export type IngredientLinesResult = Schemas['IngredientLinesResult'];
export type RecipeLookupResponse = Schemas['RecipeLookupResponse'];
export type OwnerResult = Schemas['OwnerResult'];
export interface Device { id: string; label?: string | null; created_at?: string | null; last_used_at?: string | null; expires_at?: string | null; revoked_at?: string | null; current?: boolean }
export interface Pairing { id: string; code?: string; token?: string; expires_at?: string; status?: string; display_name?: string; has_existing_profile?: boolean }
