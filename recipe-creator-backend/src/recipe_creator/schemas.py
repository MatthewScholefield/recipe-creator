from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator



MEAL_CLASSIFIERS = ('breakfast', 'lunch', 'dinner', 'dessert')


def tag_key(value: str) -> str:
    """Return the canonical lowercase kebab-case representation of a tag."""
    import unicodedata
    value = unicodedata.normalize("NFC", value).casefold()
    result = []
    pending_dash = False
    for character in value:
        if character.isalnum():
            if pending_dash and result:
                result.append("-")
            result.append(character)
            pending_dash = False
        else:
            pending_dash = True
    return "".join(result)


def validate_classifiers(tags):
    if len(set(tags) & set(MEAL_CLASSIFIERS)) > 1:
        raise ValueError('Choose only one meal type: breakfast, lunch, dinner or dessert.')
    return tags


def normalize_import_tags(tags):
    """Canonicalize untrusted legacy tags, dropping empty and duplicate results."""
    result = []
    seen = set()
    for tag in tags:
        value = tag_key(tag)
        if value and value not in seen:
            result.append(value)
            seen.add(value)
    return result


def normalize_tags(tags):
    """Validate API tags and deduplicate them without accepting noncanonical input."""
    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise ValueError('Tags must be a list of strings')
    if any(not tag or tag != tag_key(tag) for tag in tags):
        raise ValueError('Tags must use lowercase kebab-case with letters and numbers')
    result = list(dict.fromkeys(tags))
    return validate_classifiers(result)


def canonical_tag(value: str) -> str:
    if value != tag_key(value):
        raise ValueError('Tags must use lowercase kebab-case with letters and numbers')
    return value


Tag = Annotated[str, Field(min_length=1, max_length=80), AfterValidator(canonical_tag)]


ShortText = Annotated[str, Field(max_length=500)]
Prose = Annotated[str, Field(max_length=100_000)]
Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")]
Amount = Annotated[str, Field(max_length=64)]


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Ingredient(StrictDTO):
    id: Identifier = Field(default_factory=lambda: uuid4().hex)
    original_text: Annotated[str, Field(max_length=10_000)] = ""
    quantity: Amount | None = None
    quantity_max: Amount | None = None
    unit: ShortText = ""
    name: ShortText = ""
    preparation: Annotated[str, Field(max_length=2000)] = ""
    optional: bool = False
    grams: None = None

    @model_validator(mode="before")
    @classmethod
    def discard_estimates(cls, value):
        if isinstance(value, dict):
            value = {key: item for key, item in value.items() if not key.startswith("grams")}
        return value



class IngredientGroup(StrictDTO):
    id: Identifier = Field(default_factory=lambda: uuid4().hex)
    name: ShortText = ""
    ingredients: list[Ingredient] = Field(default_factory=list, max_length=300)


class RecipeDraft(StrictDTO):
    title: Annotated[str, Field(min_length=1, max_length=300)]
    source_text: Prose = ""
    mode: Literal["text", "structured"] = "text"
    description: Prose = ""
    ingredient_groups: list[IngredientGroup] = Field(default_factory=list, max_length=50)
    directions: Prose = ""
    notes: Prose = ""
    tags: list[Tag] = Field(default_factory=list, max_length=50)
    yield_amount: Amount | None = None
    yield_unit: ShortText = ""
    source_url: Annotated[str, Field(max_length=2048)] = ""
    modifications: Prose = ""

    @model_validator(mode="before")
    @classmethod
    def discard_authority(cls, value):
        if isinstance(value, dict):
            readonly = {"owner_id", "author_name", "can_edit", "photo_trust", "photo_trusted", "admin", "trusted", "total_views", "unique_viewers"}
            return {key: item for key, item in value.items() if key not in readonly}
        return value

    @field_validator('tags', mode='before')
    @classmethod
    def clean_tags(cls, value):
        return normalize_tags(value)

    @field_validator("title")
    @classmethod
    def nonblank_title(cls, value):
        if not value.strip():
            raise ValueError("Title is required")
        return value


    @field_validator("source_url")
    @classmethod
    def safe_url(cls, value):
        if not value:
            return value
        try:
            parsed = urlsplit(value)
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                raise ValueError("Invalid port")
        except ValueError:
            raise ValueError("Use an HTTP(S) URL without credentials") from None
        if (parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or "\\" in value or any(ord(ch) <= 32 or ord(ch) == 127 for ch in value)):
            raise ValueError("Use an HTTP(S) URL without credentials")
        return value

    @model_validator(mode="after")
    def bounded_unique_groups(self):
        ids = [group.id for group in self.ingredient_groups]
        rows = [row.id for group in self.ingredient_groups for row in group.ingredients]
        if len(ids) != len(set(ids)) or len(rows) != len(set(rows)):
            raise ValueError("Group and ingredient IDs must be unique")
        if len(rows) > 1000:
            raise ValueError("Too many ingredients")
        if sum(len(getattr(self, key)) for key in ("source_text", "description", "directions", "notes", "modifications")) > 300_000:
            raise ValueError("Recipe is too large")
        return self


class RecipeUpdate(RecipeDraft):
    expected_revision: int = Field(ge=1, le=2_147_483_647)


class GramEstimate(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    amount: float | str | None = None
    low: float | None = None
    high: float | None = None
    estimated: bool = True
    basis: str = ""
    refusal_reason: str | None = None


class IngredientOutput(BaseModel):
    id: str
    original_text: str
    quantity: str | None = None
    quantity_max: str | None = None
    unit: str = ""
    name: str = ""
    preparation: str = ""
    optional: bool = False
    grams: GramEstimate | None = None


class IngredientGroupOutput(BaseModel):
    id: str
    name: str
    ingredients: list[IngredientOutput]


class Recipe(BaseModel):
    id: str
    revision: int
    owner_id: str | None
    author_name: str | None
    can_edit: bool
    enrichment_status: str
    photos: list[dict] = Field(default_factory=list)
    title: str
    source_text: str
    mode: Literal["text", "structured"]
    description: str
    ingredient_groups: list[IngredientGroupOutput]
    directions: str
    notes: str
    tags: list[str]
    yield_amount: str | None
    yield_unit: str
    source_url: str
    modifications: str
    total_views: int = Field(default=0, ge=0)
    unique_viewers: int = Field(default=0, ge=0)


class RecipeViewCounts(BaseModel):
    total_views: int = Field(default=0, ge=0)
    unique_viewers: int = Field(default=0, ge=0)


class PublicUser(BaseModel):
    id: str
    display_name: str
    state: str
    trusted: bool
    merged_into: str | None = None


class AdminUser(PublicUser):
    last_login_at: datetime | None = None


class SessionResponse(BaseModel):
    user: PublicUser | None
    device_id: str | None
    admin: bool
    csrf_token: str


class AdminUsersResponse(BaseModel):
    users: list[AdminUser]
    items: list[AdminUser]
    total: int
    start: int
    limit: int
    has_more: bool


class OwnerResult(BaseModel):
    id: str
    owner_id: str | None
    author_name: str | None
    revision: int


class SiteCopy(StrictDTO):
    site_title: str = Field(max_length=80)
    site_tagline: str = Field(max_length=160)
    home_title: str = Field(max_length=120)
    home_intro: str = Field(max_length=300)
    footer_text: str = Field(max_length=200)

    @field_validator('*')
    @classmethod
    def printable(cls, value, info):
        import unicodedata
        if any(unicodedata.category(ch).startswith('C') and ch not in '\t\n\r' for ch in value):
            raise ValueError('Use printable text')
        if info.field_name in {'site_title', 'home_title'} and not value.strip():
            raise ValueError('Title is required')
        return value


DEFAULT_SITE_COPY = dict(site_title='Recipes', site_tagline='Share your food.', home_title='Recipes', home_intro='', footer_text='')


class SiteSettings(BaseModel):
    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)

    revision: int
    site_copy: SiteCopy = Field(alias='copy')


class SiteSettingsUpdate(StrictDTO):
    model_config = ConfigDict(validate_by_name=True, serialize_by_alias=True)

    expected_revision: int = Field(ge=0)
    site_copy: SiteCopy = Field(alias='copy')


class RecipeCatalogProjection(StrictDTO):
    id: Identifier
    title: str
    description: str
    search_description: str = ""
    search_directions: str = ""
    ingredient_groups: list[dict] = Field(default_factory=list)
    tags: list[str]
    owner_id: str | None
    author_name: str | None

    @field_validator("id", "owner_id", mode="before")
    @classmethod
    def bare_record_id(cls, value):
        return str(value).partition(":")[2] or str(value) if value is not None else None

    @field_validator("search_description", "search_directions", mode="before")
    @classmethod
    def missing_search_text(cls, value):
        return value or ""

    @field_validator("ingredient_groups", mode="before")
    @classmethod
    def missing_ingredient_groups(cls, value):
        return value or []


class RecipeSummary(BaseModel):
    thumbnail_photo_id: str | None = None
    id: str
    title: str
    description: str
    tags: list[str]
    owner_id: str | None
    author_name: str | None
    total_views: int = Field(default=0, ge=0)
    unique_viewers: int = Field(default=0, ge=0)
    search_score: float | None = None


class RecipeSummaryGroup(BaseModel):
    name: str
    items: list[RecipeSummary]


class RecipeListResponse(BaseModel):
    items: list[RecipeSummary]
    offset: int
    limit: int
    total: int
    has_more: bool
    errors: list[str]
    groups: list[RecipeSummaryGroup]
    popular: list[RecipeSummary] = Field(default_factory=list)


class TagCatalog(BaseModel):
    tags: list[str]
    classifier_tags: list[str]




class RecipeLookupRequest(StrictDTO):
    ids: list[Annotated[str, Field(pattern=r'^(recipes:)?[A-Za-z0-9_-]{1,160}$')]] = Field(max_length=100)
    q: str = Field(default='', max_length=2000)
    tags: list[Tag] = Field(default_factory=list, max_length=50)


class RecipeLookupResponse(BaseModel):
    items: list[RecipeSummary]
    unavailable_ids: list[str]


class IngredientLineInput(StrictDTO):
    id: Identifier
    text: str = Field(min_length=1, max_length=10_000)

    @field_validator('text')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Ingredient text is required')
        return value


class IngredientLinesRequest(StrictDTO):
    lines: list[IngredientLineInput] = Field(min_length=1, max_length=1000)

    @model_validator(mode='after')
    def bounded_unique(self):
        if len({row.id for row in self.lines}) != len(self.lines):
            raise ValueError('Ingredient IDs must be unique')
        if sum(len(row.text) for row in self.lines) > 100_000:
            raise ValueError('Ingredient text is too large')
        return self


class IngredientLineResult(BaseModel):
    id: str
    text: str
    method: Literal['llm']
    ingredient: IngredientOutput


class IngredientLinesResult(BaseModel):
    items: list[IngredientLineResult]


class ParseResult(BaseModel):
    description: str
    ingredient_groups: list[IngredientGroupOutput]
    directions: str
    notes: str
    yield_amount: str | None
    yield_unit: str
    source_url: str


class ParseRequest(StrictDTO):
    source_text: Annotated[str, Field(min_length=1, max_length=100_000)]

    @field_validator("source_text")
    @classmethod
    def nonblank_source(cls, value):
        if not value.strip():
            raise ValueError("Source text is required")
        return value
