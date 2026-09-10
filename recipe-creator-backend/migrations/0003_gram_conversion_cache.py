"""Add the global quantity-independent ingredient gram conversion cache."""
from surreal_orm.migrations import Migration
from surreal_orm.migrations.operations import RawSQL


migration = Migration(
    name="0003_gram_conversion_cache",
    dependencies=["0002_user_admin_site_settings"],
    operations=[RawSQL(sql="""
DEFINE TABLE gram_conversions SCHEMAFULL;
DEFINE FIELD created_at ON gram_conversions TYPE datetime;
DEFINE FIELD updated_at ON gram_conversions TYPE datetime;
DEFINE FIELD payload ON gram_conversions TYPE object FLEXIBLE;
DEFINE FIELD cache_key ON gram_conversions TYPE string;
DEFINE FIELD unit ON gram_conversions TYPE string;
DEFINE FIELD ingredient_label ON gram_conversions TYPE string;
DEFINE FIELD grams_per_unit_low ON gram_conversions TYPE float ASSERT $value >= 0;
DEFINE FIELD grams_per_unit_high ON gram_conversions TYPE float ASSERT $value >= 0 AND $value >= grams_per_unit_low;
DEFINE FIELD basis ON gram_conversions TYPE string;
DEFINE FIELD assumptions ON gram_conversions TYPE array<string>;
DEFINE FIELD regional_assumption ON gram_conversions TYPE string;
DEFINE FIELD model ON gram_conversions TYPE string;
""", description="Cache reusable per-unit ingredient gram ranges")],
)
