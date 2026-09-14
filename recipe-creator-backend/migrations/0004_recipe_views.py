"""Generalize user trust and add durable recipe view tracking."""
from surreal_orm.migrations import Migration
from surreal_orm.migrations.operations import RawSQL


migration = Migration(
    name="0004_recipe_views",
    dependencies=["0003_gram_conversion_cache"],
    operations=[RawSQL(sql="""
DEFINE FIELD IF NOT EXISTS trusted ON users TYPE bool DEFAULT false;
DEFINE FIELD IF NOT EXISTS viewer_id ON users TYPE option<string>;
UPDATE users SET trusted = photo_trust WHERE photo_trust != NONE;
REMOVE FIELD IF EXISTS photo_trust ON users;
UPDATE users UNSET photo_trust;

DEFINE TABLE IF NOT EXISTS viewers SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS created_at ON viewers TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON viewers TYPE datetime;
DEFINE FIELD IF NOT EXISTS payload ON viewers TYPE object FLEXIBLE;
DEFINE FIELD IF NOT EXISTS user_id ON viewers TYPE option<string>;
DEFINE FIELD IF NOT EXISTS merged_into ON viewers TYPE option<string>;
DEFINE FIELD IF NOT EXISTS revision ON viewers TYPE int DEFAULT 1 ASSERT $value >= 1;

DEFINE TABLE IF NOT EXISTS viewer_credentials SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS created_at ON viewer_credentials TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON viewer_credentials TYPE datetime;
DEFINE FIELD IF NOT EXISTS payload ON viewer_credentials TYPE object FLEXIBLE;
DEFINE FIELD IF NOT EXISTS viewer_id ON viewer_credentials TYPE string;
DEFINE FIELD IF NOT EXISTS expires_at ON viewer_credentials TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON viewer_credentials TYPE int DEFAULT 1 ASSERT $value >= 1;
DEFINE INDEX IF NOT EXISTS viewer_credentials_expiry ON viewer_credentials FIELDS expires_at;

DEFINE TABLE IF NOT EXISTS recipe_viewers SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS created_at ON recipe_viewers TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON recipe_viewers TYPE datetime;
DEFINE FIELD IF NOT EXISTS payload ON recipe_viewers TYPE object FLEXIBLE;
DEFINE FIELD IF NOT EXISTS recipe_id ON recipe_viewers TYPE record<recipes> REFERENCE ON DELETE REJECT;
DEFINE FIELD IF NOT EXISTS viewer_id ON recipe_viewers TYPE string;
DEFINE FIELD IF NOT EXISTS first_view_at ON recipe_viewers TYPE datetime;
DEFINE FIELD IF NOT EXISTS last_counted_at ON recipe_viewers TYPE datetime;
DEFINE FIELD IF NOT EXISTS first_category ON recipe_viewers TYPE string ASSERT $value IN ['trusted', 'named', 'anonymous'];
DEFINE FIELD IF NOT EXISTS revision ON recipe_viewers TYPE int DEFAULT 1 ASSERT $value >= 1;
DEFINE INDEX IF NOT EXISTS recipe_viewers_identity ON recipe_viewers FIELDS recipe_id, viewer_id UNIQUE;
DEFINE INDEX IF NOT EXISTS recipe_viewers_viewer ON recipe_viewers FIELDS viewer_id;

DEFINE TABLE IF NOT EXISTS recipe_view_stats SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS created_at ON recipe_view_stats TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON recipe_view_stats TYPE datetime;
DEFINE FIELD IF NOT EXISTS payload ON recipe_view_stats TYPE object FLEXIBLE;
DEFINE FIELD IF NOT EXISTS recipe_id ON recipe_view_stats TYPE record<recipes> REFERENCE ON DELETE REJECT;
DEFINE FIELD IF NOT EXISTS total_views ON recipe_view_stats TYPE int DEFAULT 0 ASSERT $value >= 0;
DEFINE FIELD IF NOT EXISTS unique_viewers ON recipe_view_stats TYPE int DEFAULT 0 ASSERT $value >= 0;
DEFINE FIELD IF NOT EXISTS trusted_views ON recipe_view_stats TYPE int DEFAULT 0 ASSERT $value >= 0;
DEFINE FIELD IF NOT EXISTS named_views ON recipe_view_stats TYPE int DEFAULT 0 ASSERT $value >= 0;
DEFINE FIELD IF NOT EXISTS anonymous_views ON recipe_view_stats TYPE int DEFAULT 0 ASSERT $value >= 0;
DEFINE FIELD IF NOT EXISTS trusted_unique_viewers ON recipe_view_stats TYPE int DEFAULT 0 ASSERT $value >= 0;
DEFINE FIELD IF NOT EXISTS named_unique_viewers ON recipe_view_stats TYPE int DEFAULT 0 ASSERT $value >= 0;
DEFINE FIELD IF NOT EXISTS anonymous_unique_viewers ON recipe_view_stats TYPE int DEFAULT 0 ASSERT $value >= 0;
DEFINE FIELD IF NOT EXISTS revision ON recipe_view_stats TYPE int DEFAULT 1 ASSERT $value >= 1;

DEFINE TABLE IF NOT EXISTS view_ip_windows SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS created_at ON view_ip_windows TYPE datetime;
DEFINE FIELD IF NOT EXISTS updated_at ON view_ip_windows TYPE datetime;
DEFINE FIELD IF NOT EXISTS payload ON view_ip_windows TYPE object FLEXIBLE;
DEFINE FIELD IF NOT EXISTS entries ON view_ip_windows TYPE array<object> DEFAULT [] ASSERT array::len($value) <= 10;
DEFINE FIELD IF NOT EXISTS entries.*.viewer_id ON view_ip_windows TYPE string;
DEFINE FIELD IF NOT EXISTS entries.*.last_counted_at ON view_ip_windows TYPE datetime;
DEFINE FIELD IF NOT EXISTS expires_at ON view_ip_windows TYPE datetime;
DEFINE FIELD IF NOT EXISTS revision ON view_ip_windows TYPE int DEFAULT 1 ASSERT $value >= 1;
DEFINE INDEX IF NOT EXISTS view_ip_windows_expiry ON view_ip_windows FIELDS expires_at;
""", description="Rename trust and add compact durable recipe view identities and rollups")],
)
