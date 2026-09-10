<script lang="ts">
  import { appState } from './app-state.svelte';
  import { ApiError, mutate, message, id } from './api';
  import type { AdminUser, Recipe, OwnerResult } from './types';
  import UserPicker from './UserPicker.svelte';

  let {recipe, onchanged}: {recipe: Pick<Recipe, 'id' | 'revision' | 'owner_id' | 'author_name'>; onchanged: (result: OwnerResult) => void} = $props();
  let saving = $state(false), error = $state('');

  async function choose(user: AdminUser) {
    if (saving || !appState.identity?.admin) return;
    if (user.id === recipe.owner_id) { error = ''; return; }
    const snapshot = {id: recipe.id, revision: recipe.revision};
    saving = true; error = '';
    try {
      const result = await mutate<OwnerResult>(`/admin/recipes/${id(snapshot.id)}/owner`, {owner_id: user.id, expected_revision: snapshot.revision});
      if (recipe.id !== snapshot.id || recipe.revision !== snapshot.revision) {
        error = 'This recipe changed. Reload the recipe before changing its author.';
        return;
      }
      onchanged(result);
    } catch(e) {
      error = e instanceof ApiError && e.status === 409 ? 'This recipe changed. Reload the recipe before changing its author.' : message(e);
    } finally {
      saving = false;
    }
  }
</script>

{#if appState.identity?.admin}
  <section class="author-field" aria-label="Recipe author">
    <UserPicker
      label="Author"
      selectedId={recipe.owner_id}
      selectedName={recipe.author_name}
      disabled={saving}
      eligibility="owner"
      help="Search for a profile to transfer recipe ownership."
      optionsLabel="Eligible authors"
      onselect={choose}
    />
    {#if error}<p role="alert">{error}</p>{/if}
  </section>
{/if}

<style>
  .author-field{margin:1.5rem 0;padding-top:1rem;border-top:1px solid var(--border)}
</style>
