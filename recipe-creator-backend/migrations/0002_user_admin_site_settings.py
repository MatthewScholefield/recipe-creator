"""Explicit security cutover: never infer privileges from legacy password sessions."""
from surreal_orm.migrations import Migration
from surreal_orm.migrations.operations import RawSQL

migration = Migration(
    name="0002_user_admin_site_settings",
    dependencies=["0001_initial"],
    operations=[RawSQL(sql="""
DEFINE FIELD is_admin ON users TYPE bool DEFAULT false;
UPDATE users SET is_admin = false;
UPDATE admin_sessions SET revoked_at = time::now();
DEFINE TABLE site_settings SCHEMAFULL;
DEFINE FIELD created_at ON site_settings TYPE datetime;
DEFINE FIELD updated_at ON site_settings TYPE datetime;
DEFINE FIELD payload ON site_settings TYPE object FLEXIBLE;
DEFINE FIELD revision ON site_settings TYPE int ASSERT $value >= 1;
DEFINE FIELD copy ON site_settings TYPE object;
DEFINE FIELD copy.site_title ON site_settings TYPE string ASSERT string::len($value) <= 80;
DEFINE FIELD copy.site_tagline ON site_settings TYPE string ASSERT string::len($value) <= 160;
DEFINE FIELD copy.home_title ON site_settings TYPE string ASSERT string::len($value) <= 120;
DEFINE FIELD copy.home_intro ON site_settings TYPE string ASSERT string::len($value) <= 300;
DEFINE FIELD copy.footer_text ON site_settings TYPE string ASSERT string::len($value) <= 200;
""", description="Retire password authority and add allowlisted public site copy")],
)
