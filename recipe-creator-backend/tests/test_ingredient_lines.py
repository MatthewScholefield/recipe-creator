from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from recipe_creator import ai
from recipe_creator.ingredient_lines import parse_ingredient_lines
from recipe_creator.ingredients import parse_ingredient_line
from recipe_creator.schemas import IngredientLinesRequest
from recipe_creator.settings import Settings


@pytest.mark.parametrize('text,quantity,maximum,unit,name,preparation,optional', [
    ('2 eggs', '2', None, '', 'eggs', '', False),
    ('250g flour', '250', None, 'g', 'flour', '', False),
    ('1.25 cups milk', '1.25', None, 'cup', 'milk', '', False),
    ('.5 cups milk', '0.5', None, 'cup', 'milk', '', False),
    ('1/2 tsp salt', '0.5', None, 'tsp', 'salt', '', False),
    ('1 1/2 tablespoons sugar', '1.5', None, 'tbsp', 'sugar', '', False),
    ('1½ tbsp sugar', '1.5', None, 'tbsp', 'sugar', '', False),
    ('⅓ cup milk', '0.333333333333333', None, 'cup', 'milk', '', False),
    ('1–2 onions', '1', '2', '', 'onions', '', False),
    ('1 to 2 kg potatoes', '1', '2', 'kg', 'potatoes', '', False),
    ('  1 onion, finely chopped (optional)  ', '1', None, '', 'onion', 'finely chopped', True),
    ('salt', None, None, '', 'salt', '', False),
    ('salt to taste', None, None, '', 'salt', 'to taste', False),
])
def test_deterministic_grammar(text, quantity, maximum, unit, name, preparation, optional):
    assert parse_ingredient_line(text) == {
        'quantity': quantity, 'quantity_max': maximum, 'unit': unit, 'name': name,
        'preparation': preparation, 'optional': optional, 'grams': None,
    }


@pytest.mark.parametrize('text', ['2 cans tomatoes', '1 (400g) can beans', '1/0 cup milk', '-2 eggs',
                                 '2–1 eggs', '1 + 2 eggs', '2x eggs', 'NaN 2 cups', '', '1/2/3 cups flour'])
def test_uncertain_grammar(text):
    assert parse_ingredient_line(text) is None


async def test_reported_parenthetical_equivalents_are_organized_without_ai(monkeypatch):
    fallback = AsyncMock(side_effect=AssertionError('no fallback'))
    reserve = AsyncMock()
    monkeypatch.setattr(ai, 'parse_ingredient_lines_batch', fallback)
    texts = [
        '2 ¾ cups (385 g) all purpose gluten free flour blend',
        '⅗ cup (72 g) tapioca starch/flour',
        '1 tablespoon (9 g) instant yeast',
        '1 (6 g) teaspoon kosher salt',
        '1 (25 g) egg white',
        '6 tablespoons (84 g) unsalted butter',
        '1 ⅜ cups (11 ounces) warm water',
    ]
    result = await parse_ingredient_lines(
        [{'id': str(index), 'text': text} for index, text in enumerate(texts)],
        Settings(),
        reserve_fallback=reserve,
    )
    assert [(item.ingredient.quantity, item.ingredient.unit, item.ingredient.name) for item in result.items] == [
        ('2.75', 'cup', 'all purpose gluten free flour blend'),
        ('0.6', 'cup', 'tapioca starch/flour'),
        ('1', 'tbsp', 'instant yeast'),
        ('1', 'tsp', 'kosher salt'),
        ('1', '', 'egg white'),
        ('6', 'tbsp', 'unsalted butter'),
        ('1.375', 'cup', 'warm water'),
    ]
    assert all(item.method == 'deterministic' for item in result.items)
    fallback.assert_not_called()
    reserve.assert_not_called()


async def test_no_ai_for_deterministic(monkeypatch):
    fallback = AsyncMock(side_effect=AssertionError('no fallback'))
    reserve = AsyncMock()
    monkeypatch.setattr(ai, 'parse_ingredient_lines_batch', fallback)
    lines = [{'id': 'legacy', 'text': '  2 eggs\r\n'}]
    result = await parse_ingredient_lines(lines, Settings(), reserve_fallback=reserve)
    assert result.items[0].text == result.items[0].ingredient.original_text == lines[0]['text']
    assert result.items[0].ingredient.id == 'legacy'
    assert result.items[0].ingredient.grams is None
    assert result.items[0].method == 'deterministic'
    fallback.assert_not_called()
    reserve.assert_not_called()


async def test_exactly_one_batch_and_committed_reservation(monkeypatch):
    lines = [{'id': f'old-{i}', 'text': '1 (400g) can beans'} for i in range(100)]
    events = []
    async def reserve():
        events.append('reserved')
    async def fallback(rows, settings):
        assert events == ['reserved']
        events.append('called')
        assert rows == lines
        return {'items': [{'id': row['id'], 'unparsed': True} for row in rows]}
    monkeypatch.setattr(ai, 'parse_ingredient_lines_batch', fallback)
    result = await parse_ingredient_lines(lines, Settings(), reserve_fallback=reserve)
    assert events == ['reserved', 'called']
    assert [item.id for item in result.items] == [row['id'] for row in lines]
    assert all(item.method == 'unparsed' and item.ingredient.name == '' for item in result.items)


@pytest.mark.parametrize('output', [
    {'items': []}, {'items': [{'id': 'foreign', 'unparsed': True}]},
    {'items': [{'id': 'row', 'unparsed': True}, {'id': 'row', 'unparsed': True}]},
    {'items': [{'id': 'row', 'name': {'start': 0, 'end': 1000}}]},
    {'items': [{'id': 'row', 'quantity': {'start': 0, 'end': 1}, 'name': {'start': 0, 'end': 3}}]},
    {'items': [{'id': 'row', 'quantity': {'start': 0, 'end': 3}, 'name': {'start': 9, 'end': 14}}]},
])
async def test_invalid_fallback_never_applies(output, monkeypatch):
    monkeypatch.setattr(ai, 'parse_ingredient_lines_batch', AsyncMock(return_value=output))
    lines = [{'id': 'row', 'text': '1/0 cups flour'}, {'id': 'safe', 'text': '2 eggs'}]
    result = await parse_ingredient_lines(lines, Settings(), reserve_fallback=AsyncMock())
    assert result.warnings == ['ingredient_fallback_invalid']
    assert result.items[0].ingredient.original_text == lines[0]['text']
    assert result.items[0].ingredient.quantity is None and result.items[0].ingredient.name == ''
    assert result.items[1].method == 'deterministic'


async def test_valid_source_spans_preserve_exact_text(monkeypatch):
    # A deliberately conservative deterministic rejection can be safely named verbatim.
    text = '2 cans tomatoes'
    output = {'items': [{'id': 'legacy', 'name': {'start': 0, 'end': len(text)}}]}
    monkeypatch.setattr(ai, 'parse_ingredient_lines_batch', AsyncMock(return_value=output))
    result = await parse_ingredient_lines([{'id': 'legacy', 'text': text}], Settings(), reserve_fallback=AsyncMock())
    assert result.items[0].method == 'llm'
    assert result.items[0].ingredient.name == text
    assert result.items[0].ingredient.quantity is None and result.items[0].ingredient.grams is None


async def test_unaccounted_source_is_allowed(monkeypatch):
    text = '1/0 cups flour'
    output = {'items': [{'id': 'row', 'name': {'start': 9, 'end': 14}}]}
    monkeypatch.setattr(ai, 'parse_ingredient_lines_batch', AsyncMock(return_value=output))
    result = await parse_ingredient_lines(
        [{'id': 'row', 'text': text}], Settings(), reserve_fallback=AsyncMock()
    )
    assert result.items[0].method == 'llm'
    assert result.items[0].ingredient.name == 'flour'
    assert result.items[0].ingredient.original_text == text


async def test_timeout_and_quota(monkeypatch):
    fallback = AsyncMock(side_effect=TimeoutError('provider'))
    monkeypatch.setattr(ai, 'parse_ingredient_lines_batch', fallback)
    lines = [{'id': 'row', 'text': '2 cans tomatoes'}]
    result = await parse_ingredient_lines(lines, Settings(), reserve_fallback=AsyncMock())
    assert result.warnings == ['ingredient_fallback_unavailable']
    fallback.reset_mock()
    with pytest.raises(ai.AIQuotaExceeded):
        await parse_ingredient_lines(lines, Settings(), reserve_fallback=AsyncMock(side_effect=ai.AIQuotaExceeded()))
    fallback.assert_not_called()


@pytest.mark.parametrize('lines', [[], [{'id': 'a', 'text': ' '}], [{'id': 'bad:id', 'text': 'salt'}],
                                 [{'id': 'a', 'text': 'salt'}] * 2,
                                 [{'id': str(i), 'text': 'x' * 10000} for i in range(11)],
                                 [{'id': 'a', 'text': 'x' * 10001}]])
def test_input_bounds(lines):
    with pytest.raises(ValidationError):
        IngredientLinesRequest(lines=lines)
