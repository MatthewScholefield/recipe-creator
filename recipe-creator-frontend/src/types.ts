import type { components } from './api.generated';

type Schemas = components['schemas'];

export interface User { id: string; display_name: string; photo_trusted: boolean; state: string }
export interface Session { user: User | null; device_id: string | null; admin: boolean; csrf_token: string }
export type GramEstimate = Schemas['GramEstimate'];
// Editor state keeps output-only estimates; the write API discards them.
export type Ingredient = Omit<Required<Schemas['Ingredient']>, 'grams'> & { grams: GramEstimate | null };
export type IngredientGroup = Omit<Required<Schemas['IngredientGroup']>, 'ingredients'> & { ingredients: Ingredient[] };
export type RecipeDraft = Omit<Required<Schemas['RecipeDraft']>, 'ingredient_groups'> & { ingredient_groups: IngredientGroup[] };
export interface Photo { id: string; recipe_id: string; uploader_id?: string; uploader_name?: string; caption: string; state: string; can_delete?: boolean }
export interface AdminPhoto extends Omit<Photo, 'state' | 'can_delete'> { status: 'pending' | 'approved' | 'rejected' }
export type Recipe = Omit<Schemas['Recipe'], 'ingredient_groups' | 'photos'> & { ingredient_groups: IngredientGroup[]; photos?: Photo[] };
export interface RecipeSummary { id: string; title: string; description: string; tags: string[]; author_name: string | null; owner_id?: string | null; thumbnail_photo_id?: string }
export interface ParseResult { source_hash: string; source_text?: string; description: string; ingredient_groups: IngredientGroup[]; directions: string; notes: string; unclassified: string; warnings: string[] }
export interface Device { id: string; label?: string | null; created_at?: string | null; last_used_at?: string | null; expires_at?: string | null; revoked_at?: string | null; current?: boolean }
export interface Pairing { id: string; code?: string; token?: string; expires_at?: string; status?: string; display_name?: string; has_existing_profile?: boolean }
