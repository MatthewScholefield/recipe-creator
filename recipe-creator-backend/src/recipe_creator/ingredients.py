"""Deterministic ingredient display and conservative, provenance-bound mass conversion."""
from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from fractions import Fraction
from hashlib import sha256

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
_AMBIGUOUS = re.compile(
    r"\b(?:to taste|as needed|as required|optional|or|and/or|package\w*|packets?|packs?|"
    r"cans?|tins?|jars?|bottles?|boxes?|bags?|sachets?|handfuls?|pinch\w*|dash\w*)\b", re.IGNORECASE,
)
_VOLUME = r"(?:cups?|tablespoons?|tbsp|teaspoons?|tsp|ml|millilit(?:er|re)s?|l|lit(?:er|re)s?|fl\.?\s*oz)"
_COUNTS = r"(?:cloves?|eggs?|onions?|apples?|bananas?|lemons?|limes?|potatoes|tomatoes|carrots?)"


def parse_quantity(value: str | float | None) -> tuple[float, float] | None:
    """Return an inclusive range; reject ambiguous, negative and nonfinite amounts."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace("⁄", "/")
    for glyph, fraction in _VULGAR.items():
        text = text.replace(glyph, " " + fraction)
    parts = re.split(r"\s*(?:[–—-]|\bto\b)\s*", text)
    if len(parts) not in (1, 2):
        return None

    def number(part: str) -> float:
        part = part.strip()
        if not re.fullmatch(r"(?:\d+(?:\.\d+)?|\.\d+|\d+\s*/\s*\d+|\d+\s+\d+\s*/\s*\d+)", part):
            raise ValueError("Not an amount")
        part = re.sub(r"\s*/\s*", "/", part)
        return float(sum((Fraction(p) for p in part.split()), Fraction()))

    try:
        values = [number(p) for p in parts]
    except (ValueError, ZeroDivisionError, OverflowError):
        return None
    low, high = values[0], values[-1]
    return (low, high) if 0 <= low <= high and math.isfinite(high) else None


UNIT_ALIASES = {}
for _canonical, _aliases in {
    'g': 'g gram grams', 'kg': 'kg kilogram kilograms', 'mg': 'mg milligram milligrams',
    'ml': 'ml milliliter milliliters millilitre millilitres', 'l': 'l liter liters litre litres',
    'oz': 'oz ounce ounces', 'lb': 'lb lbs pound pounds', 'cup': 'cup cups',
    'tbsp': 'tbsp tablespoon tablespoons', 'tsp': 'tsp teaspoon teaspoons',
}.items():
    UNIT_ALIASES.update(dict.fromkeys(_aliases.split(), _canonical))
UNIT_ALIASES.update({'fl oz': 'fl oz', 'fl. oz': 'fl oz', 'fluid ounce': 'fl oz', 'fluid ounces': 'fl oz'})
_NUMBER = r'(?:\d+\s+\d+\s*[/⁄]\s*\d+|\d+\s*[/⁄]\s*\d+|\d*\s*[' + ''.join(_VULGAR) + r']|\d+(?:\.\d+)?|\.\d+)'
_LINE_AMOUNT = re.compile(r'^(' + _NUMBER + r')(?:\s*(?:[–—-]|\bto\b)\s*(' + _NUMBER + r'))?')
_UNIT_PATTERN = '|'.join(re.escape(alias) for alias in sorted(UNIT_ALIASES, key=len, reverse=True))
_PARENTHETICAL_MEASURE = re.compile(rf'^\(\s*({_NUMBER})\s*({_UNIT_PATTERN})\s*\)\s*', re.IGNORECASE)
_UNCERTAIN_LINE = re.compile(r'\b(?:packages?|packets?|packs?|cans?|tins?|jars?|bottles?|boxes?|bags?|sachets?|handfuls?|pinches?|dashes?|or|and/or)\b|[×*=<>]', re.IGNORECASE)


def numeric_string(value):
    return format(value, '.15g')


def parse_ingredient_line(text: str) -> dict | None:
    """Conservative source-only grammar; uncertainty never fabricates amounts."""
    source = text.strip()
    if not source or _UNCERTAIN_LINE.search(source) or re.search(r'\b(?:nan|inf|infinity)\b|[∞]', source, re.IGNORECASE):
        return None
    optional = source.endswith('(optional)')
    if optional:
        source = source[:-10].rstrip()
    quantity = quantity_max = None
    unit = ''
    match = _LINE_AMOUNT.match(source)
    if match:
        amount = parse_quantity(match.group(1))
        high = parse_quantity(match.group(2)) if match.group(2) else None
        if not amount or amount[1] > 1_000_000_000 or (match.group(2) and (not high or high[0] < amount[0] or high[1] > 1_000_000_000)):
            return None
        quantity = numeric_string(amount[0])
        quantity_max = numeric_string(high[0]) if high else None
        remaining = source[match.end():]
        attached = bool(remaining and not remaining[0].isspace())
        remaining = remaining.lstrip()
        for alias in sorted(UNIT_ALIASES, key=len, reverse=True):
            if remaining.casefold().startswith(alias) and len(remaining) > len(alias) and remaining[len(alias)].isspace():
                unit = UNIT_ALIASES[alias]
                remaining = remaining[len(alias):].lstrip()
                break
        if attached and not unit and not remaining.startswith('('):
            return None
        equivalent = _PARENTHETICAL_MEASURE.match(remaining)
        if equivalent:
            alternate = parse_quantity(equivalent.group(1))
            if not alternate or alternate[1] > 1_000_000_000:
                return None
            remaining = remaining[equivalent.end():]
            if not unit:
                for alias in sorted(UNIT_ALIASES, key=len, reverse=True):
                    if (remaining.casefold().startswith(alias)
                            and len(remaining) > len(alias)
                            and remaining[len(alias)].isspace()):
                        unit = UNIT_ALIASES[alias]
                        remaining = remaining[len(alias):].lstrip()
                        break
        source = remaining
    elif not source[0].isalpha():
        return None
    if (not source or any(ch.isdigit() for ch in source) or '(' in source or ')' in source
            or re.search(r'(?<![A-Za-z])[/⁄]|[/⁄](?![A-Za-z])|[+–—]|(?<!\w)-(?!\w)', source)):
        return None
    name, separator, preparation = source.partition(',')
    name, preparation = name.strip(), preparation.strip()
    if not separator and name.endswith(' to taste'):
        name, preparation = name[:-9].rstrip(), 'to taste'
    if not name or len(name) > 500 or len(preparation) > 2000:
        return None
    return {'quantity': quantity, 'quantity_max': quantity_max, 'unit': unit, 'name': name,
            'preparation': preparation, 'optional': optional, 'grams': None}


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
    authored = {"text": ingredient.get("text") or "", "name": ingredient.get("name") or "",
                "quantity": ingredient.get("quantity"), "unit": ingredient.get("unit")}
    if isinstance(authored["quantity"], (int, float)) and not isinstance(authored["quantity"], bool):
        authored["quantity"] = float(authored["quantity"])
    return sha256(json.dumps(authored, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def author_confirmed(ingredient: dict) -> bool:
    provenance = ingredient.get("grams_provenance")
    if isinstance(provenance, dict):
        provenance = provenance.get("source")
    grams = ingredient.get("grams")
    return (ingredient.get("grams_confirmed") is True or provenance == "author_confirmed"
            or isinstance(grams, dict) and grams.get("source") == "author_confirmed")


def estimation_eligible(ingredient: dict) -> bool:
    """Only explicit, unambiguous ingredient-dependent volume/count amounts go to AI."""
    if author_confirmed(ingredient) or ingredient.get("grams_input_hash") == ingredient_hash(ingredient) and (
        ingredient.get("grams") is not None or ingredient.get("grams_range") is not None
    ):
        return False
    text = format_ingredient(ingredient)
    if _AMBIGUOUS.search(text) or re.search(r"[()]|(?<=[A-Za-z])\s*/\s*(?=[A-Za-z])", text):
        return False
    match = re.match(rf"^\s*(.+?)\s+({_VOLUME}|{_COUNTS})\b(.*)$", text, re.IGNORECASE)
    if not match or parse_quantity(match[1]) is None:
        return False
    # Volume without an ingredient identity has no density basis.
    return bool(match[3].strip()) or bool(re.fullmatch(_COUNTS, match[2], re.IGNORECASE))


def invalidate_estimates(ingredient: dict) -> dict:
    """Drop derived values when any authored ingredient input changes."""
    result = deepcopy(ingredient)
    if (result.get("grams_input_hash") != ingredient_hash(result)
            and not (author_confirmed(result) and not result.get("grams_input_hash"))):
        for key in _DERIVED:
            result.pop(key, None)
    return result


def _mass_from_text(text: str):
    # Only an amount followed by an explicit mass unit is convertible. No density guesses.
    units = "|".join(sorted(_MASS, key=len, reverse=True))
    match = re.match(rf"^\s*(.+?)\s*({units})\.?(?=\s|$)", text, re.IGNORECASE)
    return convert_to_grams(match[1], match[2]) if match else None


def enrich_ingredient(ingredient: dict) -> dict:
    result = invalidate_estimates(ingredient)
    result.setdefault("original_text", format_ingredient(ingredient))
    # Source text is authoritative if present; never trust stale structured fields over it.
    if author_confirmed(result):
        result.setdefault("grams_input_hash", ingredient_hash(result))
        return result
    text = format_ingredient(result)
    if _AMBIGUOUS.search(text) or re.search(r"[()]|(?<=[A-Za-z])\s*/\s*(?=[A-Za-z])", text):
        return result
    grams = (_mass_from_text(result["text"]) if result.get("text")
             else convert_to_grams(result.get("quantity"), result.get("unit")))
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
