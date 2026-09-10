import asyncio
import json

import pytest
from pydantic import ValidationError
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ThinkingPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, DeltaThinkingPart, FunctionModel
from pydantic_ai.models.test import TestModel

from recipe_creator.ai import ParseRequest, parse_recipe, parser_agent
from recipe_creator.settings import Settings


@pytest.fixture(autouse=True)
def no_paid_calls(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


def output(**kwargs):
    return {
        "description": "",
        "ingredient_groups": [],
        "directions": "",
        "notes": "",
        "yield_amount": None,
        "yield_unit": "",
        "source_url": "",
        **kwargs,
    }


def ingredient(original_text, name, **kwargs):
    return {
        "original_text": original_text,
        "quantity": None,
        "quantity_max": None,
        "unit": "",
        "name": name,
        "preparation": "",
        "optional": False,
        **kwargs,
    }


def streaming_model(function):
    async def stream(messages, info):
        response = await function(messages, info)
        for index, part in enumerate(response.parts):
            if isinstance(part, ThinkingPart):
                yield {index: DeltaThinkingPart(content=part.content)}
            elif isinstance(part, ToolCallPart):
                yield {
                    index: DeltaToolCall(
                        name=part.tool_name,
                        json_args=part.args_as_json_str(),
                        tool_call_id=part.tool_call_id,
                    )
                }
            else:
                raise AssertionError(f"Unsupported streamed test part: {part!r}")

    return FunctionModel(function, stream_function=stream)


def test_request_rejects_identity():
    with pytest.raises(ValidationError):
        ParseRequest(source_text="Recipe", display_name="private")


async def test_reformatted_recipe_fields_are_direct_output():
    source = (
        "Café pancakes\r\nMakes 4 servings\r\nhttps://example.com/pancakes\r\n"
        "Batter:\r\n- ½ kg flour\r\n- 2 eggs\r\nSauce:\r\n- 1 lemon\r\n"
        "1) Mix gently.\r\n2) Bake at 180°C for 20 minutes.\r\nNote: Keep cool."
    )
    expected = output(
        description="Café pancakes",
        ingredient_groups=[
            {
                "name": "Batter",
                "ingredients": [
                    ingredient("½ kg flour", "flour", quantity="0.5", unit="kg"),
                    ingredient("2 eggs", "eggs", quantity="2"),
                ],
            },
            {
                "name": "Sauce",
                "ingredients": [ingredient("1 lemon", "lemon", quantity="1")],
            },
        ],
        directions="Mix gently.\n\nBake at 180°C for 20 minutes.",
        notes="Keep cool.",
        yield_amount="4",
        yield_unit="servings",
        source_url="https://example.com/pancakes",
    )
    with parser_agent.override(model=TestModel(custom_output_args=expected)):
        data = await parse_recipe(source, Settings())
    assert data == expected




async def test_ingredient_batch_one_request_no_transport_retries(monkeypatch):
    import openai
    from recipe_creator import ai
    from pydantic_ai.exceptions import UnexpectedModelBehavior
    original = openai.AsyncOpenAI
    retries = []
    def client(*args, **kwargs):
        retries.append(kwargs['max_retries'])
        return original(*args, **kwargs)
    monkeypatch.setattr(openai, 'AsyncOpenAI', client)
    lines = [{'id': 'legacy', 'text': '2 cans tomatoes'}]
    calls = []
    async def respond(messages, info):
        payload = json.loads(next(p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)))
        assert payload == lines
        calls.append(1)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'items': [{'id': 'legacy', **ingredient('2 cans tomatoes', 'tomatoes', quantity='2', unit='cans')}]})])
    with ai.ingredient_line_agent.override(model=FunctionModel(respond)):
        result = await ai.parse_ingredient_lines_batch(lines, Settings())
    assert result.items[0].name == "tomatoes" and calls == [1] and retries == [0]
    calls.clear()
    async def invalid(messages, info):
        calls.append(1)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'not_items': 'repair me'})])
    with ai.ingredient_line_agent.override(model=FunctionModel(invalid)):
        with pytest.raises(UnexpectedModelBehavior):
            await ai.parse_ingredient_lines_batch(lines, Settings())
    assert calls == [1]


async def test_ingredient_groups_require_labels_and_use_model_organization_directly():
    source = "Filling:\n2 apples\nTopping:\n1 cup oats"
    calls = []

    async def respond(_messages, info):
        calls.append(1)
        filling_name = "" if len(calls) == 1 else "Filling"
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output(
            ingredient_groups=[
                {
                    "name": filling_name,
                    "ingredients": [ingredient("2 apples", "apples", quantity="2")],
                },
                {
                    "name": "Topping",
                    "ingredients": [ingredient("1 cup oats", "oats", quantity="1", unit="cup")],
                },
            ],
        ))])

    with parser_agent.override(model=streaming_model(respond)):
        result = await parse_recipe(source, Settings())
    assert len(calls) == 2
    assert result["ingredient_groups"] == [
        {"name": "Filling", "ingredients": [ingredient("2 apples", "apples", quantity="2")]},
        {"name": "Topping", "ingredients": [ingredient("1 cup oats", "oats", quantity="1", unit="cup")]},
    ]


async def test_single_group_label_is_returned():
    expected = output(ingredient_groups=[{
        "name": "Ingredients",
        "ingredients": [ingredient("2 eggs", "eggs", quantity="2")],
    }])
    with parser_agent.override(model=TestModel(custom_output_args=expected)):
        result = await parse_recipe("2 eggs", Settings())
    assert result["ingredient_groups"][0]["name"] == "Ingredients"


async def test_only_source_sent_and_validation_retries():
    from pydantic_ai.exceptions import UnexpectedModelBehavior

    source = "Mix now."
    calls = []

    async def respond(messages, info):
        prompts = [
            p.content
            for m in messages
            for p in m.parts
            if isinstance(p, UserPromptPart)
        ]
        assert json.loads(prompts[0]) == {"source_text": source}
        assert not info.allow_text_output
        calls.append(1)
        directions = [{"invalid": True}] if len(calls) == 1 else source
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    output(directions=directions),
                )
            ]
        )

    with parser_agent.override(model=streaming_model(respond)):
        result = await parse_recipe(
            source, Settings(db_user="private-name", ai_api_key="private-key")
        )
    assert len(calls) == 2
    assert result["directions"] == source

    failed_calls = []

    async def invalid(messages, info):
        failed_calls.append(1)
        return ModelResponse(
            parts=[
                ToolCallPart(
                    info.output_tools[0].name,
                    output(directions=[{"invalid": True}]),
                )
            ]
        )

    with parser_agent.override(model=streaming_model(invalid)):
        with pytest.raises(UnexpectedModelBehavior):
            await parse_recipe(source, Settings())
    assert len(failed_calls) == 2


async def test_parser_prints_live_thinking(capsys):
    source = "Mix now."

    async def respond(messages, info):
        return ModelResponse(
            parts=[
                ThinkingPart("considering the recipe"),
                ToolCallPart(info.output_tools[0].name, output(directions=source)),
            ]
        )

    with parser_agent.override(model=streaming_model(respond)):
        await parse_recipe(source, Settings())
    assert capsys.readouterr().out == "THOUGHTS: considering the recipe"


async def test_timeout_and_concurrency():
    active = maximum = 0

    async def respond(messages, info):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(.02)
            return ModelResponse(
                parts=[ToolCallPart(info.output_tools[0].name, output())]
            )
        finally:
            active -= 1

    with parser_agent.override(model=streaming_model(respond)):
        await asyncio.gather(
            *(parse_recipe("x", Settings(ai_concurrency=1)) for _ in range(3))
        )
        assert maximum == 1
        with pytest.raises(TimeoutError):
            await parse_recipe("x", Settings(ai_timeout_seconds=.001))
    assert active == 0


async def test_estimator_batches_typed_quantity_independent_bases():
    from recipe_creator.ai import estimate_gram_bases, estimator_agent
    from recipe_creator.ingredients import REGIONAL_ASSUMPTION

    items = [
        {"cache_key": "a" * 64, "unit": "cup", "ingredient_label": "tapioca starch"},
        {"cache_key": "b" * 64, "unit": "egg", "ingredient_label": "egg"},
    ]
    calls = []

    async def respond(messages, info):
        payload = json.loads(next(
            p.content for message in messages for p in message.parts
            if isinstance(p, UserPromptPart)
        ))
        assert payload == {"items": items, "regional_assumption": REGIONAL_ASSUMPTION}
        calls.append(1)
        estimates = [
            {"cache_key": item["cache_key"], "grams_per_unit_low": 110,
             "grams_per_unit_high": 125, "basis": f"One {item['unit']}",
             "assumptions": ["Typical preparation"]}
            for item in items
        ]
        if len(calls) == 1:
            estimates[0]["cache_key"] = "c" * 64
        data = {"regional_assumption": REGIONAL_ASSUMPTION, "items": estimates}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, data)])

    with estimator_agent.override(model=FunctionModel(respond)):
        result = await estimate_gram_bases(items, Settings(ai_model="openai:test-model"))
    assert len(calls) == 2
    assert [item["cache_key"] for item in result] == ["a" * 64, "b" * 64]
    assert all(item["model"] == "openai:test-model" for item in result)


@pytest.mark.parametrize("change", [
    {"grams_per_unit_low": -1},
    {"grams_per_unit_high": float("inf")},
    {"grams_per_unit_low": 200},
    {"grams_per_unit_high": None},
    {"assumptions": [" "]},
    {"refusal_reason": "uncertain"},
])
def test_invalid_estimates_are_rejected(change):
    from recipe_creator.ai import (
        GramEstimateBatchOutput,
        GramEstimateBatchRequest,
        validate_gram_estimates,
    )
    from recipe_creator.ingredients import REGIONAL_ASSUMPTION

    request = GramEstimateBatchRequest(items=[{
        "cache_key": "a" * 64, "unit": "cup", "ingredient_label": "flour",
    }])
    item = {
        "cache_key": "a" * 64,
        "grams_per_unit_low": 110,
        "grams_per_unit_high": 130,
        "basis": "Flour density",
        "assumptions": ["Level cup"],
        **change,
    }
    with pytest.raises(ValueError):
        output = GramEstimateBatchOutput.model_validate({
            "regional_assumption": REGIONAL_ASSUMPTION,
            "items": [item],
        })
        validate_gram_estimates(request, output)


@pytest.mark.parametrize("missing", ["grams_per_unit_low", "grams_per_unit_high"])
def test_estimate_bounds_are_required(missing):
    from recipe_creator.ai import GramEstimateBatchOutput
    from recipe_creator.ingredients import REGIONAL_ASSUMPTION

    item = {
        "cache_key": "a" * 64,
        "grams_per_unit_low": 110,
        "grams_per_unit_high": 130,
        "basis": "Flour density",
        "assumptions": ["Level cup"],
    }
    item.pop(missing)
    with pytest.raises(ValidationError):
        GramEstimateBatchOutput.model_validate({
            "regional_assumption": REGIONAL_ASSUMPTION,
            "items": [item],
        })


async def test_estimator_refusal():
    from recipe_creator.ai import estimate_gram_bases, estimator_agent
    from recipe_creator.ingredients import REGIONAL_ASSUMPTION

    item = {"cache_key": "a" * 64, "unit": "cup", "ingredient_label": "unknown flour"}
    refusal = {
        "regional_assumption": REGIONAL_ASSUMPTION,
        "items": [{
            "cache_key": item["cache_key"],
            "grams_per_unit_low": None,
            "grams_per_unit_high": None,
            "basis": "Density unknown",
            "assumptions": ["No density assumed"],
            "refusal_reason": "Unknown ingredient",
        }],
    }
    with estimator_agent.override(model=TestModel(custom_output_args=refusal)):
        result = await estimate_gram_bases([item], Settings())
    assert result[0]["refusal_reason"] == "Unknown ingredient"


async def test_openai_alias_and_runtime_reused():
    from recipe_creator.ai import _runtime

    settings = Settings(ai_model="openai:deepseek-v4-flash-0731", ai_base_url="https://crof.ai/v1")
    model, semaphore = _runtime(settings)
    assert model.model_name == "deepseek-v4-flash-0731"
    assert _runtime(settings) == (model, semaphore)


async def test_estimator_timeout_uses_bounded_runtime():
    from recipe_creator.ai import estimate_gram_bases, estimator_agent
    async def respond(messages, info):
        await asyncio.sleep(1)
    with estimator_agent.override(model=FunctionModel(respond)):
        with pytest.raises(TimeoutError):
            await estimate_gram_bases(
                [{"cache_key": "a" * 64, "unit": "cup", "ingredient_label": "flour"}],
                Settings(ai_timeout_seconds=.001),
            )
