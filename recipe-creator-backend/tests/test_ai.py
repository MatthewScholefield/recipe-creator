import asyncio
import json

import pytest
from pydantic import ValidationError
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ThinkingPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, DeltaThinkingPart, FunctionModel
from pydantic_ai.models.test import TestModel

from recipe_creator.ai import ParseRequest, parse_recipe, parser_agent, source_hash
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
        "unclassified": "",
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


async def test_reformatted_unicode_and_whitespace_are_direct_output():
    source = (
        "Café pancakes\r\nBatter:\r\n- ½ kg flour\r\n- 2 eggs\r\nSauce:\r\n"
        "- 1 lemon\r\n1) Mix  gently.\r\n2) Bake at 180°C for 20 minutes.\r\n"
        "Note: Keep cool.\r\n??? handwritten mark"
    )
    expected = output(
        description="Café pancakes",
        ingredient_groups=[
            {
                "name": "Batter",
                "ingredients": [
                    {"original_text": "½ kg flour"},
                    {"original_text": "2 eggs"},
                ],
            },
            {
                "name": "Sauce",
                "ingredients": [{"original_text": "1 lemon"}],
            },
        ],
        directions="Mix gently.\n\nBake at 180°C for 20 minutes.",
        notes="Keep cool.",
        unclassified="??? handwritten mark",
    )
    with parser_agent.override(model=TestModel(custom_output_args=expected)):
        data = await parse_recipe(source, Settings())
    assert data["description"] == "Café pancakes"
    assert data["ingredient_groups"] == expected["ingredient_groups"]
    assert data["directions"] == expected["directions"]
    assert data["notes"] == "Keep cool."
    assert data["unclassified"] == "??? handwritten mark"
    assert data["source_text"] == source
    assert data["source_hash"] == source_hash(source)
    assert data["warnings"] == ["unclassified_source"]


async def test_empty_unclassified_has_no_warning():
    source = "Mix now."
    expected = output(directions="Mix now.")
    with parser_agent.override(model=TestModel(custom_output_args=expected)):
        data = await parse_recipe(source, Settings())
    assert data["unclassified"] == ""
    assert data["warnings"] == []


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
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'items': [{'id': 'legacy', 'unparsed': True}]})])
    with ai.ingredient_line_agent.override(model=FunctionModel(respond)):
        result = await ai.parse_ingredient_lines_batch(lines, Settings())
    assert result.items[0].unparsed and calls == [1] and retries == [0]
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
                    "ingredients": [{"original_text": "2 apples"}],
                },
                {
                    "name": "Topping",
                    "ingredients": [{"original_text": "1 cup oats"}],
                },
            ],
        ))])

    with parser_agent.override(model=streaming_model(respond)):
        result = await parse_recipe(source, Settings())
    assert len(calls) == 2
    assert result["ingredient_groups"] == [
        {"name": "Filling", "ingredients": [{"original_text": "2 apples"}]},
        {"name": "Topping", "ingredients": [{"original_text": "1 cup oats"}]},
    ]


async def test_single_group_label_is_returned():
    expected = output(ingredient_groups=[{
        "name": "Ingredients",
        "ingredients": [{"original_text": "2 eggs"}],
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


async def test_estimator_typed_separate_hash_bound_and_private():
    from recipe_creator.ai import estimate_grams, estimator_agent
    from recipe_creator.ingredients import REGIONAL_ASSUMPTION, ingredient_hash

    ingredient = {"text": "1 cup flour", "private_author": "private"}
    calls = []

    async def respond(messages, info):
        payload = json.loads(next(p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)))
        assert set(payload) == {"ingredient", "input_hash", "regional_assumption"}
        assert set(payload["ingredient"]) == {"text", "quantity", "unit", "name"}
        assert payload["regional_assumption"] == REGIONAL_ASSUMPTION
        calls.append(1)
        data = {"input_hash": "0" * 64 if len(calls) == 1 else ingredient_hash(ingredient),
                "grams_low": 115, "grams_high": 130, "basis": "Loose all-purpose flour density",
                "regional_assumption": REGIONAL_ASSUMPTION,
                "assumptions": ["Spoon-filled, level US cup", "All-purpose wheat flour"]}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, data)])

    with estimator_agent.override(model=FunctionModel(respond)):
        result = await estimate_grams(ingredient, Settings(ai_model="openai:test-model"))
    assert len(calls) == 2
    assert result["model"] == "openai:test-model"
    assert result["grams_low"] == 115
    assert ingredient == {"text": "1 cup flour", "private_author": "private"}


@pytest.mark.parametrize("change", [
    {"grams_low": -1}, {"grams_high": float("inf")}, {"grams_low": 200},
    {"grams_high": None}, {"regional_assumption": "UK"}, {"assumptions": [" "]},
    {"refusal_reason": "uncertain"}, {"input_hash": "0" * 64},
])
def test_invalid_estimates_are_rejected(change):
    from recipe_creator.ai import GramEstimateOutput, GramEstimateRequest, validate_gram_estimate
    from recipe_creator.ingredients import REGIONAL_ASSUMPTION, ingredient_hash

    item = {"text": "1 cup flour"}
    request = GramEstimateRequest(ingredient=item, input_hash=ingredient_hash(item))
    values = {"input_hash": request.input_hash, "grams_low": 110, "grams_high": 130,
              "basis": "Flour density", "assumptions": ["Level cup"],
              "regional_assumption": REGIONAL_ASSUMPTION, **change}
    with pytest.raises(ValueError):
        validate_gram_estimate(request, GramEstimateOutput.model_validate(values))


async def test_estimator_refusal_and_ineligible_no_network():
    from recipe_creator.ai import estimate_grams, estimator_agent
    from recipe_creator.ingredients import REGIONAL_ASSUMPTION, ingredient_hash

    item = {"text": "1 cup unknown flour"}
    refusal = {"input_hash": ingredient_hash(item), "grams_low": None, "grams_high": None,
               "basis": "Ingredient density unknown", "assumptions": ["No density assumed"],
               "regional_assumption": REGIONAL_ASSUMPTION, "refusal_reason": "Unknown ingredient"}
    with estimator_agent.override(model=TestModel(custom_output_args=refusal)):
        assert (await estimate_grams(item, Settings()))["refusal_reason"] == "Unknown ingredient"
    for text in ("salt to taste", "1 package flour", "1 cup flour or sugar", "200 g flour"):
        with pytest.raises(ValueError, match="not eligible"):
            await estimate_grams({"text": text}, Settings())


async def test_openai_alias_and_runtime_reused():
    from recipe_creator.ai import _runtime

    settings = Settings(ai_model="openai:deepseek-v4-flash-0731", ai_base_url="https://crof.ai/v1")
    model, semaphore = _runtime(settings)
    assert model.model_name == "deepseek-v4-flash-0731"
    assert _runtime(settings) == (model, semaphore)


async def test_estimator_timeout_uses_bounded_runtime():
    from recipe_creator.ai import estimate_grams, estimator_agent
    async def respond(messages, info):
        await asyncio.sleep(1)
    with estimator_agent.override(model=FunctionModel(respond)):
        with pytest.raises(TimeoutError):
            await estimate_grams({"text": "1 cup flour"}, Settings(ai_timeout_seconds=.001))
