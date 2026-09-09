"""Ingredient preview: deterministic first, one source-span-only batch fallback."""

from logly import logger
from pydantic import ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior

from . import ai
from .ingredients import UNIT_ALIASES, numeric_string, parse_ingredient_line, parse_quantity
from .schemas import Ingredient, IngredientLinesResult


def materialize_line(text, row):
    fields = ('quantity', 'quantity_max', 'unit', 'name', 'preparation', 'optional')
    if row.unparsed:
        if any(getattr(row, field) is not None for field in fields):
            raise ValueError('Unparsed row has spans')
        return None
    spans, values = [], {}
    for field in fields:
        span = getattr(row, field)
        if span is None:
            values[field] = None
            continue
        if not 0 <= span.start < span.end <= len(text):
            raise ValueError('Invalid source span')
        spans.append((span.start, span.end))
        values[field] = text[span.start:span.end]
    spans.sort()
    end = 0
    for start, stop in spans:
        if start < end:
            raise ValueError('Overlapping source spans')
        end = stop
    if not values['name'] or not values['name'].strip():
        raise ValueError('Missing ingredient name')
    for field in ('quantity', 'quantity_max'):
        if values[field] is not None:
            parsed = parse_quantity(values[field])
            if not parsed or parsed[0] != parsed[1] or parsed[1] > 1_000_000_000:
                raise ValueError('Invalid amount')
            values[field] = numeric_string(parsed[0])
    unit = values['unit']
    if unit is not None and unit.strip().casefold() not in UNIT_ALIASES:
        raise ValueError('Unknown unit')
    marker = values['optional']
    if marker is not None and marker != '(optional)':
        raise ValueError('Invalid optional marker')
    ingredient = Ingredient(id=row.id, original_text=text, quantity=values['quantity'],
                            quantity_max=values['quantity_max'], unit=UNIT_ALIASES[unit.strip().casefold()] if unit else '',
                            name=values['name'], preparation=values['preparation'] or '', optional=marker is not None)
    return ingredient.model_dump(exclude={'id', 'original_text'})


async def parse_ingredient_lines(lines, settings, *, reserve_fallback) -> IngredientLinesResult:
    lines = [line.model_dump() if hasattr(line, 'model_dump') else line for line in lines]
    parsed = {line['id']: parse_ingredient_line(line['text']) for line in lines}
    uncertain = [line for line in lines if parsed[line['id']] is None]
    fallback, warnings = {}, []
    if uncertain:
        await reserve_fallback()  # A committed quota reservation precedes any external request.
        try:
            output = await ai.parse_ingredient_lines_batch(uncertain, settings)
            output = ai.IngredientBatchOutput.model_validate(output)
            expected = {line['id']: line['text'] for line in uncertain}
            ids = [row.id for row in output.items]
            if len(ids) != len(set(ids)) or set(ids) != set(expected):
                raise ValueError('Fallback IDs do not match')
            # Validate the entire batch before exposing any fallback result.
            fallback = {row.id: materialize_line(expected[row.id], row) for row in output.items}
        except (ValueError, ValidationError, UnexpectedModelBehavior) as exc:
            logger.exception('Ingredient fallback validation failed ({})', type(exc).__name__)
            warnings.append('ingredient_fallback_invalid')
        except Exception as exc:
            logger.exception('Ingredient fallback unavailable ({})', type(exc).__name__)
            warnings.append('ingredient_fallback_unavailable')
    items = []
    for line in lines:
        value = parsed[line['id']] or fallback.get(line['id'])
        method = 'deterministic' if parsed[line['id']] else 'llm' if value else 'unparsed'
        ingredient = {'id': line['id'], 'original_text': line['text'], 'grams': None,
                      **(value or {'quantity': None, 'quantity_max': None, 'unit': '', 'name': '',
                                   'preparation': '', 'optional': False})}
        items.append({**line, 'method': method, 'ingredient': ingredient})
    return IngredientLinesResult(items=items, warnings=warnings)
