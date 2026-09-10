"""LLM-backed ingredient organization."""

from . import ai
from .schemas import IngredientLinesResult




async def parse_ingredient_lines(lines, settings, *, reserve_fallback) -> IngredientLinesResult:
    lines = [line.model_dump() if hasattr(line, 'model_dump') else line for line in lines]
    await reserve_fallback()
    output = await ai.parse_ingredient_lines_batch(lines, settings)
    return IngredientLinesResult(items=[
        {
            "id": row.id,
            "text": row.original_text,
            "method": "llm",
            "ingredient": {**row.model_dump(), "grams": None},
        }
        for row in output.items
    ])
