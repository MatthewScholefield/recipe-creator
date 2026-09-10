from copy import deepcopy

import pytest

from recipe_creator.ingredients import (
    convert_to_grams, enrich_ingredient, enrich_ingredient_groups,
    format_ingredient, format_recipe, gram_estimation_basis, ingredient_hash,
    invalidate_estimates, parse_quantity,
)


@pytest.mark.parametrize("value,expected", [
    ("1 1/2", (1.5, 1.5)), ("1½", (1.5, 1.5)), ("⅔", (2/3, 2/3)),
    ("1/2–3/4", (.5, .75)), ("2 to 3", (2, 3)), ("1⁄2", (.5, .5)),
    ("1 / 2", (.5, .5)), (0, (0, 0)),
    ("-1", None), ("1/0", None), ("nan", None), (float("inf"), None),
    ("3-2", None), ("about 2", None), (True, None), ("1-2-3", None),
])
def test_quantities(value, expected):
    assert parse_quantity(value) == expected


def test_mass_only_conversion_and_range():
    assert convert_to_grams("½", "kg") == (500, 500)
    assert convert_to_grams(2, "oz")[0] == pytest.approx(56.69904625)
    assert convert_to_grams(1, "lb.")[0] == pytest.approx(453.59237)
    assert convert_to_grams("1–2", "g") == (1, 2)
    assert convert_to_grams(1, "cup") is None
    assert convert_to_grams(1, "fl oz") is None
    assert convert_to_grams(1, "clove") is None


def test_original_text_never_rewritten_and_estimates_invalidated():
    ingredient = {"text": "  ½ kg flour  ", "quantity": "1", "unit": "cup", "name": "flour"}
    result = enrich_ingredient(ingredient)
    assert result["grams"] == 500
    assert result["grams_estimate"] is False
    assert result["original_text"] == ingredient["text"]
    assert format_ingredient(result) == ingredient["text"]
    assert "grams" not in ingredient
    changed = {**result, "text": "2 cups flour"}
    assert "grams" not in enrich_ingredient(changed)
    assert invalidate_estimates(result) == result
    assert "grams" not in invalidate_estimates({**result, "name": "sugar"})


def test_ranges_are_not_silently_averaged():
    result = enrich_ingredient({"text": "1–2 oz butter"})
    assert result["grams"] is None
    assert result["grams_range"] == pytest.approx([28.349523125, 56.69904625])


def test_existing_estimate_requires_matching_provenance():
    item = {"name": "flour", "quantity": 1, "unit": "cup"}
    estimate = {**item, "grams": 120, "grams_estimate": True, "grams_provenance": "estimate",
                "grams_input_hash": ingredient_hash(item)}
    assert enrich_ingredient(estimate)["grams"] == 120
    assert "grams" not in enrich_ingredient({**estimate, "quantity": 2})


def test_groups_and_formatter_are_stable_and_non_mutating():
    recipe = {"description": "Cake", "ingredient_groups": [{"title": "Batter", "ingredients": [
        {"quantity": "1/2", "unit": "kg", "name": "flour"}, {"text": "pinch salt"},
    ]}], "directions": [{"text": "Mix."}], "notes": ["Keep cool."]}
    original = deepcopy(recipe)
    groups = enrich_ingredient_groups(recipe["ingredient_groups"])
    assert groups[0]["ingredients"][0]["grams"] == 500
    assert format_recipe(recipe) == "Cake\n\nBatter\n1/2 kg flour\npinch salt\n\nMix.\n\nKeep cool."
    assert recipe == original
    assert format_recipe({**recipe, "source_text": "\nraw original\r\n"}) == "\nraw original\r\n"


@pytest.mark.parametrize("text", ["1 cup flour", "½ cup sugar", "2 eggs", "2 cloves garlic",
                                     "1–2 tbsp oil", "250 ml milk"])
def test_volume_count_estimation_is_ingredient_dependent(text):
    from recipe_creator.ingredients import estimation_eligible
    assert estimation_eligible(enrich_ingredient({"text": text}))


def test_estimation_basis_normalizes_quantity_independent_cache_identity():
    half = gram_estimation_basis({"text": "0.5 cups Tapioca   Starch"})
    one_and_half = gram_estimation_basis({"text": "1.5 cup tapioca starch"})
    assert half == {
        "unit": "cup", "ingredient_label": "tapioca starch",
        "quantity_low": .5, "quantity_high": .5,
    }
    assert {
        key: one_and_half[key] for key in ("unit", "ingredient_label")
    } == {
        key: half[key] for key in ("unit", "ingredient_label")
    }
    assert (one_and_half["quantity_low"], one_and_half["quantity_high"]) == (1.5, 1.5)


@pytest.mark.parametrize("text", ["salt to taste", "1 package sugar", "1 can tomatoes",
    "1 cup flour or sugar", "1 cup", "100 g flour or 200 g sugar", "1 cup flour (200 g)",
    "100 g flour/sugar", "100 g salt as needed", "2–1 cups flour", "a few eggs"])
def test_ambiguous_inputs_never_get_estimates(text):
    from recipe_creator.ingredients import estimation_eligible
    item = enrich_ingredient({"text": text})
    assert "grams" not in item
    assert not estimation_eligible(item)


@pytest.mark.parametrize("grams", [123, {"value": 123, "source": "author_confirmed"}])
def test_author_confirmed_survives_enrichment_but_invalidates_on_edit(grams):
    from recipe_creator.ingredients import estimation_eligible
    item = {"text": "100 g flour", "grams": grams, "grams_provenance": "author_confirmed"}
    enriched = enrich_ingredient(item)
    assert enriched["grams"] == grams
    assert not estimation_eligible(enriched)
    edited = enrich_ingredient({**enriched, "text": "1 cup flour"})
    assert "grams" not in edited
    assert "grams_provenance" not in edited
    assert estimation_eligible(edited)


def test_input_hash_survives_persistence_defaults():
    item = {"text": "1 cup flour"}
    assert ingredient_hash(item) == ingredient_hash({**item, "name": "", "quantity": None,
                                                   "unit": None, "grams": None})
    assert ingredient_hash({"quantity": 1}) == ingredient_hash({"quantity": 1.0})
