from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from recipe_creator import ai
from recipe_creator.ingredient_lines import parse_ingredient_lines
from recipe_creator.schemas import IngredientLinesRequest
from recipe_creator.settings import Settings


async def test_llm_output_is_used_directly_after_quota_reservation(monkeypatch):
    events = []

    async def reserve():
        events.append("reserved")

    async def organize(lines, settings):
        events.append("called")
        assert lines == [{"id": "row", "text": "tomatoes 2 cans"}]
        return ai.IngredientBatchOutput(items=[ai.ParsedIngredientLine(
            id="row",
            original_text="2 cans tomatoes",
            quantity="2",
            unit="cans",
            name="tomatoes",
        )])

    monkeypatch.setattr(ai, "parse_ingredient_lines_batch", organize)
    result = await parse_ingredient_lines(
        [{"id": "row", "text": "tomatoes 2 cans"}],
        Settings(),
        reserve_fallback=reserve,
    )

    assert events == ["reserved", "called"]
    assert result.model_dump() == {
        "items": [{
            "id": "row",
            "text": "2 cans tomatoes",
            "method": "llm",
            "ingredient": {
                "id": "row",
                "original_text": "2 cans tomatoes",
                "quantity": "2",
                "quantity_max": None,
                "unit": "cans",
                "name": "tomatoes",
                "preparation": "",
                "optional": False,
                "grams": None,
            },
        }],
    }


async def test_provider_and_quota_failures_propagate(monkeypatch):
    provider = AsyncMock(side_effect=TimeoutError("provider"))
    monkeypatch.setattr(ai, "parse_ingredient_lines_batch", provider)
    lines = [{"id": "row", "text": "2 cans tomatoes"}]

    with pytest.raises(TimeoutError):
        await parse_ingredient_lines(lines, Settings(), reserve_fallback=AsyncMock())
    provider.reset_mock()
    with pytest.raises(ai.AIQuotaExceeded):
        await parse_ingredient_lines(
            lines,
            Settings(),
            reserve_fallback=AsyncMock(side_effect=ai.AIQuotaExceeded()),
        )
    provider.assert_not_called()


@pytest.mark.parametrize("lines", [
    [],
    [{"id": "a", "text": " "}],
    [{"id": "bad:id", "text": "salt"}],
    [{"id": "a", "text": "salt"}, {"id": "a", "text": "pepper"}],
    [{"id": "a", "text": "x" * 10_001}],
])
def test_input_bounds(lines):
    with pytest.raises(ValidationError):
        IngredientLinesRequest(lines=lines)
