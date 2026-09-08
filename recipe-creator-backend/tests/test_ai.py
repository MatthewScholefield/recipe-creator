import asyncio
import json

import pytest
from pydantic import ValidationError
from pydantic_ai import models
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from recipe_creator.ai import ParseRequest, materialize, parse_recipe, parser_agent, source_hash
from recipe_creator.settings import Settings


@pytest.fixture(autouse=True)
def no_paid_calls(monkeypatch):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)


def output(source, **kwargs):
    return {"source_hash": source_hash(source), **kwargs}


def test_request_rejects_identity_and_generated_prose():
    with pytest.raises(ValidationError):
        ParseRequest(source_text="Recipe", display_name="private")
    with pytest.raises(ValidationError):
        materialize("Recipe", output("Recipe", description="invented"))
    with pytest.raises(ValidationError):
        materialize("Recipe", {**output("Recipe"), "title": "invented"})


@pytest.mark.parametrize("change", [
    {"source_hash": "0" * 64},
    {"directions": [{"start": 0, "end": 99}]},
    {"directions": [{"start": 3, "end": 3}]},
    {"directions": [{"start": 4, "end": 6}, {"start": 0, "end": 2}]},
    {"description": [{"start": 0, "end": 3}], "notes": [{"start": 2, "end": 4}]},
    {"ingredient_groups": [{"ingredients": [{"start": 4, "end": 6}]},
                           {"ingredients": [{"start": 0, "end": 2}]}]},
    {"notes": [{"start": True, "end": 2}]},
])
def test_invalid_spans_rejected(change):
    with pytest.raises(ValueError):
        materialize("abcdef", {**output("abcdef"), **change})


def test_lossless_unicode_and_whitespace():
    source = "🍋 Café\r\nSauce:\n½ kg flour\n\nMix!\nsecret note"
    start = source.index("½")
    end = source.index("\n", start)
    data = materialize(source, output(source, ingredient_groups=[{
        "title": {"start": source.index("Sauce"), "end": source.index(":") + 1},
        "ingredients": [{"start": start, "end": end}],
    }], directions=[{"start": source.index("Mix"), "end": source.index("!", end) + 1}]))
    group = data["ingredient_groups"][0]
    pieces = data["unclassified"] + group["ingredients"] + data["direction_spans"]
    pieces.append({"text": group["title"], "span": group["title_span"]})
    assert "".join(p["text"] for p in sorted(pieces, key=lambda p: p["span"]["start"])) == source
    assert group["ingredients"][0]["original_text"] == "½ kg flour"
    assert data["warnings"] == ["unclassified_source"]


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


async def test_tool_output_without_network():
    source = "250 g flour"
    with parser_agent.override(model=TestModel(custom_output_args=output(source, ingredient_groups=[{
        "ingredients": [{"start": 0, "end": len(source)}],
    }]))):
        result = await parse_recipe(source, Settings())
    assert result["ingredient_groups"][0]["ingredients"][0]["text"] == source


async def test_only_source_sent_and_validation_retries():
    source = "Mix now."
    calls = []

    async def respond(messages, info):
        prompts = [p.content for m in messages for p in m.parts if isinstance(p, UserPromptPart)]
        payload = json.loads(prompts[0])
        assert payload == {"source_text": source, "source_hash": source_hash(source)}
        assert not info.allow_text_output
        calls.append(1)
        data = output(source, directions=[{"start": 0, "end": 99 if len(calls) == 1 else len(source)}])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, data)])

    with parser_agent.override(model=FunctionModel(respond)):
        result = await parse_recipe(source, Settings(db_user="private-name", ai_api_key="private-key"))
    assert len(calls) == 2
    assert result["directions"] == source


async def test_timeout_and_concurrency():
    active = maximum = 0

    async def respond(messages, info):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        try:
            await asyncio.sleep(.02)
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, output("x"))])
        finally:
            active -= 1

    with parser_agent.override(model=FunctionModel(respond)):
        await asyncio.gather(*(parse_recipe("x", Settings(ai_concurrency=1)) for _ in range(3)))
        assert maximum == 1
        with pytest.raises(TimeoutError):
            await parse_recipe("x", Settings(ai_timeout_seconds=.001))
    assert active == 0


def test_parse_strings_add_only_block_delimiters():
    source = "  Mix\r\nslowly!\nOTHER\nBake.\nKeep  cool."
    spans = [{"start": 0, "end": source.index("\nOTHER")},
             {"start": source.index("Bake"), "end": source.index("Bake") + 5}]
    result = materialize(source, output(source, directions=spans, notes=[{
        "start": source.index("Keep"), "end": len(source),
    }]))
    assert result["directions"] == "  Mix\r\nslowly!\n\nBake."
    assert result["notes"] == "Keep  cool."
    assert result["source_text"] == source
    assert [p["span"] for p in result["direction_spans"]] == spans
    assert any("OTHER" in p["text"] for p in result["unclassified"])


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
