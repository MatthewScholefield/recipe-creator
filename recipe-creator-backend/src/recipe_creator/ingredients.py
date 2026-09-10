"""Deterministic ingredient display and conservative, provenance-bound mass conversion."""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from hashlib import sha256
from unicodedata import normalize

_VULGAR = {"¼": "1/4", "½": "1/2", "¾": "3/4", "⅐": "1/7", "⅑": "1/9",
           "⅒": "1/10", "⅓": "1/3", "⅔": "2/3", "⅕": "1/5", "⅖": "2/5",
           "⅗": "3/5", "⅘": "4/5", "⅙": "1/6", "⅚": "5/6", "⅛": "1/8",
           "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"}
_MASS = {"g": 1, "gram": 1, "grams": 1, "kg": 1000, "kilogram": 1000,
         "kilograms": 1000, "mg": .001, "milligram": .001, "milligrams": .001,
         "oz": 28.349523125, "ounce": 28.349523125, "ounces": 28.349523125,
         "lb": 453.59237, "lbs": 453.59237, "pound": 453.59237, "pounds": 453.59237}
_DERIVED = ("grams", "grams_range", "grams_estimate", "grams_provenance", "grams_input_hash",
            "grams_error", "grams_confirmed")
REGIONAL_ASSUMPTION = "US customary: cup=236.5882365 ml, tbsp=14.7867648 ml, tsp=4.9289216 ml"
_UNIT_ALIASES = {
    "cup": "cup", "cups": "cup",
    "tablespoon": "tbsp", "tablespoons": "tbsp", "tbsp": "tbsp",
    "teaspoon": "tsp", "teaspoons": "tsp", "tsp": "tsp",
    "ml": "ml", "milliliter": "ml", "milliliters": "ml",
    "millilitre": "ml", "millilitres": "ml",
    "l": "l", "liter": "l", "liters": "l", "litre": "l", "litres": "l",
    "fl oz": "fl oz", "fl. oz": "fl oz",
    "clove": "clove", "cloves": "clove", "egg": "egg", "eggs": "egg",
    "onion": "onion", "onions": "onion", "apple": "apple", "apples": "apple",
    "banana": "banana", "bananas": "banana", "lemon": "lemon", "lemons": "lemon",
    "lime": "lime", "limes": "lime", "potato": "potato", "potatoes": "potato",
    "tomato": "tomato", "tomatoes": "tomato", "carrot": "carrot", "carrots": "carrot",
}
_VOLUME_UNITS = {"cup", "tbsp", "tsp", "ml", "l", "fl oz"}
_ESTIMATION_UNITS = "|".join(
    re.escape(unit) for unit in sorted(_UNIT_ALIASES, key=len, reverse=True)
)


def _quantity_parts(value: str | float) -> list[str]:
    text = str(value).strip().replace("⁄", "/")
    for glyph, fraction in _VULGAR.items():
        text = text.replace(glyph, " " + fraction)
    return re.split(r"\s*(?:[–—-]|\bto\b)\s*", text)


def _parse_rational(part: str) -> Fraction:
    part = part.strip()
    if not re.fullmatch(
        r"(?:\d+(?:\.\d+)?|\.\d+|\d+\s*/\s*\d+|\d+\s+\d+\s*/\s*\d+)",
        part,
    ):
        raise ValueError("Not an amount")
    part = re.sub(r"\s*/\s*", "/", part)
    return sum((Fraction(piece) for piece in part.split()), Fraction())


def _parse_quantity_rational(
    value: str | float | None,
) -> tuple[Fraction, Fraction] | None:
    if value is None or isinstance(value, bool):
        return None
    parts = _quantity_parts(value)
    if len(parts) not in (1, 2):
        return None
    try:
        values = [_parse_rational(part) for part in parts]
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    low, high = values[0], values[-1]
    return (low, high) if 0 <= low <= high else None


def parse_quantity(value: str | float | None) -> tuple[float, float] | None:
    """Return an inclusive range; reject ambiguous, negative and nonfinite amounts."""
    amount = _parse_quantity_rational(value)
    if amount is None:
        return None
    try:
        result = tuple(float(endpoint) for endpoint in amount)
    except (OverflowError, ValueError):
        return None
    return result if all(math.isfinite(endpoint) for endpoint in result) else None


def _plain_decimal(value: Fraction, source: str) -> str | None:
    source = source.strip()
    if "/" in source:
        with localcontext(Context(prec=17, rounding=ROUND_HALF_EVEN)):
            decimal = Decimal(value.numerator) / Decimal(value.denominator)
    else:
        decimal = Decimal(source)
    text = format(decimal, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    text = text or "0"
    try:
        usable = len(text) <= 64 and math.isfinite(float(text))
    except (OverflowError, ValueError):
        usable = False
    return text if usable else None


def normalize_quantity_fields(
    quantity: str | None, quantity_max: str | None
) -> tuple[str | None, str | None]:
    """Normalize valid structured scalar amounts without guessing invalid ranges."""
    amount = _parse_quantity_rational(quantity)
    if quantity is None or amount is None:
        return quantity, quantity_max

    parts = _quantity_parts(quantity)
    if len(parts) == 2:
        if quantity_max is not None:
            maximum = _parse_quantity_rational(quantity_max)
            maximum_parts = _quantity_parts(quantity_max)
            if (
                maximum is None
                or len(maximum_parts) != 1
                or maximum[0] != amount[1]
            ):
                return quantity, quantity_max
        normalized = (
            _plain_decimal(amount[0], parts[0]),
            _plain_decimal(amount[1], parts[1]),
        )
        return normalized if all(value is not None for value in normalized) else (
            quantity,
            quantity_max,
        )

    low = _plain_decimal(amount[0], parts[0])
    if low is None:
        return quantity, quantity_max
    if quantity_max is None:
        return low, None

    maximum = _parse_quantity_rational(quantity_max)
    maximum_parts = _quantity_parts(quantity_max)
    if (
        maximum is None
        or len(maximum_parts) != 1
        or maximum[0] < amount[0]
    ):
        return quantity, quantity_max
    high = _plain_decimal(maximum[0], maximum_parts[0])
    return (low, high) if high is not None else (quantity, quantity_max)


def convert_to_grams(quantity, unit: str | None) -> tuple[float, float] | None:
    amount = parse_quantity(quantity)
    factor = _MASS.get((unit or "").strip().lower().rstrip("."))
    if amount is None or factor is None:
        return None
    result = (amount[0] * factor, amount[1] * factor)
    return result if all(math.isfinite(v) for v in result) else None


def format_ingredient(ingredient: dict) -> str:
    """Authored text wins, byte-for-byte; otherwise join authored structured fields."""
    if ingredient.get("text"):
        return ingredient["text"]
    return " ".join(str(ingredient[k]) for k in ("quantity", "unit", "name")
                    if ingredient.get(k) is not None and ingredient[k] != "")


def ingredient_hash(ingredient: dict) -> str:
    # Match persistence defaults so a DB round trip cannot change the input digest.
    authored = {
        "text": ingredient.get("text") or "",
        "name": ingredient.get("name") or "",
        "quantity": ingredient.get("quantity"),
        "quantity_max": ingredient.get("quantity_max"),
        "unit": ingredient.get("unit") or "",
        "preparation": ingredient.get("preparation") or "",
        "optional": ingredient.get("optional") is True,
    }
    for key in ("quantity", "quantity_max"):
        if isinstance(authored[key], (int, float)) and not isinstance(authored[key], bool):
            authored[key] = float(authored[key])
    return sha256(json.dumps(authored, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def author_confirmed(ingredient: dict) -> bool:
    provenance = ingredient.get("grams_provenance")
    if isinstance(provenance, dict):
        provenance = provenance.get("source")
    grams = ingredient.get("grams")
    return (ingredient.get("grams_confirmed") is True or provenance == "author_confirmed"
            or isinstance(grams, dict) and grams.get("source") == "author_confirmed")


def _leading_quantity(text: str) -> tuple[tuple[float, float], str] | None:
    """Split the longest valid numeric quantity prefix from otherwise unknown units."""
    for boundary in reversed([match.start() for match in re.finditer(r"\s+\S", text)]):
        amount = parse_quantity(text[:boundary])
        if amount is not None:
            return amount, text[boundary:].strip()
    return None


def gram_estimation_basis(ingredient: dict) -> dict:
    """Return a conversion key and multiplier for every unresolved ingredient."""
    text = format_ingredient(ingredient).strip()
    amount = parse_quantity(ingredient.get("quantity"))
    maximum = parse_quantity(ingredient.get("quantity_max"))
    if amount is not None:
        low, high = amount
        if maximum is not None and maximum[0] >= low:
            high = maximum[-1]
        raw_unit = " ".join(str(ingredient.get("unit") or "").casefold().split()).rstrip(".")
        unit = _UNIT_ALIASES.get(raw_unit, raw_unit or "item")
        label_parts = (ingredient.get("name"), ingredient.get("preparation"))
        label = ", ".join(str(part).strip() for part in label_parts if str(part or "").strip())
        if not label:
            label = text
    else:
        match = re.match(rf"^\s*(.+?)\s+({_ESTIMATION_UNITS})\b(.*)$", text, re.IGNORECASE)
        parsed = parse_quantity(match[1]) if match else None
        if match is not None and parsed is not None:
            low, high = parsed
            raw_unit = re.sub(r"\s+", " ", match[2].strip().casefold()).rstrip(".")
            unit = _UNIT_ALIASES[raw_unit]
            tail = match[3].strip()
            label = tail if unit in _VOLUME_UNITS else f"{unit}{(' ' + tail) if tail else ''}"
        elif leading := _leading_quantity(text):
            (low, high), label = leading
            unit = "item"
        else:
            low = high = 1
            unit = "unspecified amount"
            label = text
    label = " ".join(normalize("NFKC", label).casefold().split()).strip(" ,")
    return {
        "unit": unit,
        "ingredient_label": label or "unspecified ingredient",
        "quantity_low": low,
        "quantity_high": high,
    }


def estimation_eligible(ingredient: dict) -> bool:
    """Send every unresolved ingredient to AI; only a model refusal may omit grams."""
    if author_confirmed(ingredient):
        return False
    if ingredient.get("grams_input_hash") == ingredient_hash(ingredient):
        return not (
            ingredient.get("grams") is not None
            or ingredient.get("grams_range") is not None
            or ingredient.get("grams_error") == "estimation_refused"
        )
    return True


def invalidate_estimates(ingredient: dict) -> dict:
    """Drop derived values when any authored ingredient input changes."""
    result = deepcopy(ingredient)
    if (result.get("grams_input_hash") != ingredient_hash(result)
            and not (author_confirmed(result) and not result.get("grams_input_hash"))):
        for key in _DERIVED:
            result.pop(key, None)
    return result


def _mass_from_text(text: str):
    units = "|".join(sorted(_MASS, key=len, reverse=True))
    match = re.match(rf"^\s*(.+?)\s*({units})\.?(?=\s|$)", text, re.IGNORECASE)
    return convert_to_grams(match[1], match[2]) if match else None


def _parenthetical_mass(text: str):
    units = "|".join(sorted(_MASS, key=len, reverse=True))
    matches = re.findall(rf"\(\s*(.+?)\s*({units})\.?\s*\)", text, re.IGNORECASE)
    return convert_to_grams(*matches[0]) if len(matches) == 1 else None


def enrich_ingredient(ingredient: dict) -> dict:
    result = invalidate_estimates(ingredient)
    result.setdefault("original_text", format_ingredient(ingredient))
    # Source text is authoritative if present; never trust stale structured fields over it.
    if author_confirmed(result):
        result.setdefault("grams_input_hash", ingredient_hash(result))
        return result
    text = format_ingredient(result)
    embedded_grams = _parenthetical_mass(text) if result.get("text") else None
    if result.get("text"):
        grams = embedded_grams or _mass_from_text(result["text"])
    else:
        grams = convert_to_grams(result.get("quantity"), result.get("unit"))
    if grams is not None:
        for key in _DERIVED:
            result.pop(key, None)
        result.update(grams=grams[0] if grams[0] == grams[1] else None,
                      grams_range=list(grams), grams_estimate=False,
                      grams_provenance="mass_conversion", grams_input_hash=ingredient_hash(result))
    return result


def enrich_ingredient_groups(groups: list[dict]) -> list[dict]:
    return [{**deepcopy(group), "ingredients": [enrich_ingredient(item)
             for item in group.get("ingredients", [])]} for group in groups]


def format_recipe(recipe: dict) -> str:
    """A stable fallback formatter; never rewrites a retained source document."""
    if recipe.get("source_text"):
        return recipe["source_text"]
    blocks = []
    for key in ("description",):
        if recipe.get(key):
            blocks.append(str(recipe[key]))
    for group in recipe.get("ingredient_groups", []):
        lines = ([group["title"]] if group.get("title") else [])
        lines.extend(format_ingredient(item) for item in group.get("ingredients", []))
        if lines:
            blocks.append("\n".join(lines))
    for key in ("directions", "notes"):
        value = recipe.get(key, [])
        if isinstance(value, str):
            value = [value]
        blocks.extend(item["text"] if isinstance(item, dict) else str(item) for item in value)
    if not recipe.get("directions") and recipe.get("instructions"):
        blocks.append(recipe["instructions"])
    return "\n\n".join(blocks)
