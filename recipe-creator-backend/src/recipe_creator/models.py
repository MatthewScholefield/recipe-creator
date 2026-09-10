"""Schemafull domain registry; unknown service fields live in flexible payload.

References explicitly REJECT deletion. Soft-delete/merge identities instead of
cascading away authored content or immutable historical attribution.
"""
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from surreal_orm import BaseSurrealModel, SurrealConfigDict
from surreal_orm.fields import ForeignKey
from surreal_orm.types import SchemaMode


def utcnow() -> datetime:
    return datetime.now(UTC)


class Ingredient(BaseModel):
    model_config = ConfigDict(extra="allow")
    text: str = ""
    name: str = ""
    quantity: float | str | None = None
    unit: str | None = None
    grams: dict[str, Any] | Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None


class IngredientGroup(BaseModel):
    model_config = ConfigDict(extra="allow")
    title: str = ""
    ingredients: list[Ingredient] = Field(default_factory=list)


class DomainModel(BaseSurrealModel):
    model_config = SurrealConfigDict(schema_mode=SchemaMode.SCHEMAFULL, extra="forbid")
    id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    payload: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"flexible": True})


class User(DomainModel):
    model_config = SurrealConfigDict(table_name="users")
    display_name: str = "Anonymous"
    state: str = "active"
    photo_trust: bool = False
    is_admin: bool = False
    merged_into: ForeignKey("User", on_delete="PROTECT") = None


class DeviceCredential(DomainModel):
    model_config = SurrealConfigDict(table_name="devices")
    user_id: ForeignKey("User", on_delete="PROTECT") = None
    secret_hash: str = ""
    label: str | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class PairingSession(DomainModel):
    model_config = SurrealConfigDict(table_name="pairings")
    source_user_id: ForeignKey("User", on_delete="PROTECT") = None
    source_device_id: ForeignKey("DeviceCredential", on_delete="PROTECT") = None
    token_hash: str = ""
    code_hash: str = ""
    status: str = "pending"
    attempts: int = Field(default=0, ge=0)
    expires_at: datetime | None = None
    revision: int = Field(default=1, ge=1)


class AdminSession(DomainModel):
    model_config = SurrealConfigDict(table_name="admin_sessions")
    secret_hash: str = ""
    expires_at: datetime | None = None
    revoked_at: datetime | None = None


class Recipe(DomainModel):
    model_config = SurrealConfigDict(table_name="recipes")
    owner_id: ForeignKey("User", on_delete="PROTECT") = None
    title: str = ""
    mode: str = "source"
    status: str = "draft"
    source_text: str = ""
    instructions: str = ""
    description: str = ""
    ingredient_groups: list[IngredientGroup] = Field(default_factory=list, json_schema_extra={"flexible": True})
    tags: list[str] = Field(default_factory=list)
    servings: float | None = Field(default=None, gt=0)
    revision: int = Field(default=1, ge=1)
    deleted_at: datetime | None = None


class RecipeRevision(DomainModel):
    model_config = SurrealConfigDict(table_name="revisions")
    recipe_id: ForeignKey("Recipe", on_delete="PROTECT") = None
    actor_id: ForeignKey("User", on_delete="PROTECT") = None
    revision: int = Field(default=1, ge=1)
    reason: str = "edit"
    content: dict[str, Any] = Field(default_factory=dict, json_schema_extra={"flexible": True})


class Photo(DomainModel):
    model_config = SurrealConfigDict(table_name="photos")
    recipe_id: ForeignKey("Recipe", on_delete="PROTECT") = None
    uploader_id: ForeignKey("User", on_delete="PROTECT") = None
    storage_key: str = ""
    thumbnail_key: str = ""
    width: int = Field(default=0, ge=0)
    height: int = Field(default=0, ge=0)
    size_bytes: int = Field(default=0, ge=0)
    caption: str = ""
    status: str = "pending"
    moderation_reason: str | None = None
    moderated_at: datetime | None = None


class EnrichmentJob(DomainModel):
    model_config = SurrealConfigDict(table_name="jobs")
    recipe_id: ForeignKey("Recipe", on_delete="PROTECT") = None
    kind: str = "grams"
    input_revision: int = Field(default=1, ge=1)
    input_hash: str = ""
    dedupe_key: str | None = None
    state: str = "pending"
    attempts: int = Field(default=0, ge=0)
    lease_until: datetime | None = None
    available_at: datetime = Field(default_factory=utcnow)
    revision: int = Field(default=1, ge=1)



class GramConversion(DomainModel):
    model_config = SurrealConfigDict(table_name="gram_conversions")
    cache_key: str = ""
    unit: str = ""
    ingredient_label: str = ""
    grams_per_unit_low: float = Field(default=0, ge=0, allow_inf_nan=False)
    grams_per_unit_high: float = Field(default=0, ge=0, allow_inf_nan=False)
    basis: str = ""
    assumptions: list[str] = Field(default_factory=list)
    regional_assumption: str = ""
    model: str = ""

    @model_validator(mode="after")
    def ordered_range(self):
        if self.grams_per_unit_high < self.grams_per_unit_low:
            raise ValueError("grams_per_unit_high must not be below grams_per_unit_low")
        return self


class AuditEvent(DomainModel):
    model_config = SurrealConfigDict(table_name="audit")
    actor_id: ForeignKey("User", on_delete="PROTECT") = None
    action: str = ""
    target: str = ""


class UsageBucket(DomainModel):
    model_config = SurrealConfigDict(table_name="usage")
    scope: str = ""
    kind: str = ""
    count: int = Field(default=0, ge=0)
    expires_at: datetime | None = None
    revision: int = Field(default=1, ge=1)


class SiteSettings(DomainModel):
    model_config = SurrealConfigDict(table_name="site_settings", validate_by_name=True, serialize_by_alias=True)
    revision: int = Field(default=1, ge=1)
    site_copy: dict[str, str] = Field(default_factory=dict, alias="copy")


MODELS = {model.get_table_name(): model for model in (
    User, DeviceCredential, PairingSession, AdminSession, Recipe,
    RecipeRevision, Photo, EnrichmentJob, GramConversion, AuditEvent, UsageBucket, SiteSettings,
)}
