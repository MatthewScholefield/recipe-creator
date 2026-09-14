import asyncio
import io
import json
import os
import stat
import tarfile
from uuid import uuid4

import pytest

from recipe_creator import cli
from recipe_creator.models import MODELS
from recipe_creator.repository import Repository
from recipe_creator.settings import Settings


@pytest.fixture
def settings(tmp_path):
    media = tmp_path / "media"
    media.mkdir()
    (media / ("a" * 32 + ".jpg")).write_bytes(b"test-image-content")
    return Settings(media_root=media, db_password="never-archive-this",
                    admin_password_hash="never-archive-admin", ai_api_key="never-archive-provider")


@pytest.fixture
def fake_export(monkeypatch):
    monkeypatch.setattr(cli, "_check_versions", lambda *args: None)

    def export(settings, binary, operation, path):
        assert operation == "export"
        path.write_text("-- test supported logical export\nDEFINE TABLE recipes SCHEMAFULL;\n")

    monkeypatch.setattr(cli, "_surreal", export)


def test_backup_manifest_private_archive_and_restore(tmp_path, settings, fake_export, monkeypatch):
    archive = tmp_path / "backup.tar.gz"
    manifest = cli.backup(settings, archive, quiesced=True)
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert manifest["versions"]["surrealdb"] == "3.2.4"
    assert manifest["versions"]["packages"]["surrealdb-orm"] == "0.33.1"
    assert set(manifest["files"]) == {"database.surql", "media/" + "a" * 32 + ".jpg"}
    with tarfile.open(archive) as tar:
        contents = b"".join(tar.extractfile(member).read() for member in tar if member.isfile())
    assert b"never-archive" not in contents
    calls = []
    monkeypatch.setattr(cli, "_require_fresh_database", lambda _: calls.append("fresh"))

    def import_database(settings, binary, operation, path):
        assert operation == "import"
        assert path.read_text().startswith("-- test supported")
        assert not settings.media_root.exists()
        calls.append("import")

    monkeypatch.setattr(cli, "_surreal", import_database)
    destination = settings.model_copy(update={"media_root": tmp_path / "restored"})
    assert cli.restore(destination, archive, quiesced=True, trusted=True) == manifest
    assert calls == ["fresh", "import"]
    assert (destination.media_root / ("a" * 32 + ".jpg")).read_bytes() == b"test-image-content"
    with pytest.raises(ValueError, match="absent"):
        cli.restore(destination, archive, quiesced=True, trusted=True)


def test_explicit_offline_and_trust_confirmations(settings, tmp_path):
    with pytest.raises(ValueError, match="quiesced"):
        cli.backup(settings, tmp_path / "backup.tar.gz")
    with pytest.raises(ValueError, match="trusted"):
        cli.restore(settings, tmp_path / "backup.tar.gz", quiesced=True)
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["backup", "archive.tar.gz"])
    with pytest.raises(SystemExit):
        cli.parser().parse_args(["restore", "archive.tar.gz", "--confirm-quiesced"])


def _archive(path, members):
    with tarfile.open(path, "w:gz") as tar:
        for name, data, kind in members:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.REGTYPE:
                info.size = len(data)
            elif kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                info.linkname = "/etc/passwd"
            tar.addfile(info, io.BytesIO(data) if kind == tarfile.REGTYPE else None)


@pytest.mark.parametrize("name,kind", [
    ("../escape", tarfile.REGTYPE), ("/absolute", tarfile.REGTYPE),
    ("media/../escape", tarfile.REGTYPE), ("media\\escape", tarfile.REGTYPE),
    ("database.surql", tarfile.SYMTYPE), ("database.surql", tarfile.LNKTYPE),
    ("database.surql", tarfile.FIFOTYPE), (".env", tarfile.REGTYPE),
])
def test_archive_path_link_special_file_checks(tmp_path, name, kind):
    archive = tmp_path / "bad.tar.gz"
    _archive(archive, [(name, b"bad", kind)])
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ValueError):
        cli.verify_archive(archive, stage)
    assert not list(stage.iterdir())


def test_duplicate_archive_members_rejected(tmp_path):
    archive = tmp_path / "bad.tar.gz"
    _archive(archive, [("database.surql", b"x", tarfile.REGTYPE)] * 2)
    with pytest.raises(ValueError, match="Duplicate"):
        cli.verify_archive(archive, tmp_path)


@pytest.mark.parametrize("mutation", ["checksum", "missing_media", "version", "unlisted", "missing_db"])
def test_manifest_verified_before_database_contact(tmp_path, settings, fake_export, monkeypatch, mutation):
    archive = tmp_path / "backup.tar.gz"
    manifest = cli.backup(settings, archive, quiesced=True)
    with tarfile.open(archive) as tar:
        members = [(member.name, tar.extractfile(member).read() if member.isfile() else b"", member.type)
                   for member in tar]
    if mutation == "checksum":
        manifest["files"]["database.surql"]["sha256"] = "0" * 64
    elif mutation == "version":
        manifest["versions"]["surrealdb"] = "0.0.0"
    elif mutation == "unlisted":
        members.append(("media/" + "b" * 32 + ".jpg", b"unlisted", tarfile.REGTYPE))
    elif mutation == "missing_db":
        members = [member for member in members if member[0] != "database.surql"]
    elif mutation == "missing_media":
        members = [member for member in members if member[0] != "media"]
    members = [(name, json.dumps(manifest).encode() if name == "manifest.json" else data, kind)
               for name, data, kind in members]
    _archive(archive, members)
    monkeypatch.setattr(cli, "_check_versions", lambda *args: pytest.fail("Contacted DB before verification"))
    monkeypatch.setattr(cli, "_require_fresh_database", lambda *args: pytest.fail("Contacted DB before verification"))
    destination = settings.model_copy(update={"media_root": tmp_path / "restore"})
    with pytest.raises(ValueError):
        cli.restore(destination, archive, quiesced=True, trusted=True)
    assert not destination.media_root.exists()


def test_backup_refuses_media_symlinks_and_config(tmp_path, settings, fake_export):
    secret = settings.media_root / ".env"
    secret.write_text("secret")
    with pytest.raises(ValueError, match="Unexpected media"):
        cli.backup(settings, tmp_path / "backup.tar.gz", quiesced=True)
    secret.unlink()
    (settings.media_root / ("b" * 32 + ".jpg")).symlink_to(tmp_path / "private")
    with pytest.raises(ValueError, match="Symlinks"):
        cli.backup(settings, tmp_path / "backup.tar.gz", quiesced=True)


def test_cli_credentials_not_in_argv_or_errors(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("SURREAL_TOKEN", "should-not-inherit")
    def run(command, **kwargs):
        assert "never-archive-this" not in command
        assert kwargs["env"]["SURREAL_PASS"] == "never-archive-this"
        assert "SURREAL_TOKEN" not in kwargs["env"]
        assert command[-4:-1] == ["--only", "--tables=true", "--records=true"]
        return type("Result", (), {"returncode": 1})()
    monkeypatch.setattr(cli.subprocess, "run", run)
    with pytest.raises(ValueError, match="output withheld"):
        cli._surreal(settings, "surreal", "export", tmp_path / "database.surql")


def test_user_admin_and_cli_commands():
    with pytest.raises(SystemExit):
        cli.parser().parse_args(['hash-password'])
    for command in ('migrate', 'cleanup', 'export-openapi', 'admin-users', 'admin-grant'):
        assert cli.parser().parse_args([command]).command == command
    args = cli.parser().parse_args(['admin-revoke', '--user-id', 'verified-user', '--yes'])
    assert args.user_id == 'verified-user' and args.yes


def test_failed_import_does_not_install_media(tmp_path, settings, fake_export, monkeypatch):
    archive = tmp_path / "backup.tar.gz"
    cli.backup(settings, archive, quiesced=True)
    monkeypatch.setattr(cli, "_require_fresh_database", lambda _: None)
    def fail(*args):
        raise ValueError("failed import")
    monkeypatch.setattr(cli, "_surreal", fail)
    destination = settings.model_copy(update={"media_root": tmp_path / "restored"})
    with pytest.raises(ValueError, match="keep application offline"):
        cli.restore(destination, archive, quiesced=True, trusted=True)
    assert not destination.media_root.exists()


@pytest.mark.integration
def test_real_backup_restore_complete_database_and_media(tmp_path):
    url = os.getenv("RECIPE_TEST_DB_URL")
    binary = os.getenv("RECIPE_TEST_SURREAL_BINARY", "surreal")
    if not url:
        pytest.skip("Set RECIPE_TEST_DB_URL and RECIPE_TEST_SURREAL_BINARY for real restore")
    media = tmp_path / "media"
    media.mkdir()
    photo_key, thumb_key = "a" * 32 + ".jpg", "a" * 32 + "_thumb.jpg"
    (media / photo_key).write_bytes(b"original test image")
    (media / thumb_key).write_bytes(b"test thumbnail")
    settings = Settings(db_url=url, db_user=os.getenv("RECIPE_TEST_DB_USER", "root"),
                        db_password=os.getenv("RECIPE_TEST_DB_PASSWORD", ""), media_root=media,
                        db_namespace="recipe_tests", db_database="backup_" + uuid4().hex)
    destination = settings.model_copy(update={"db_database": "restored_" + uuid4().hex,
                                             "media_root": tmp_path / "restored-media"})

    async def seed():
        async with Repository(settings) as repo:
            await repo.migrate()
            user = await repo.create("users", {"display_name": "Restore Author", "trusted": True})
            recipe = await repo.create("recipes", {"title": "Restore Recipe", "owner_id": user["id"],
                                                   "directions": "exact prose\n\n", "tags": ["Dinner"]})
            await repo.create("devices", {"user_id": user["id"], "secret_hash": "test-digest"})
            await repo.create("photos", {"recipe_id": recipe["id"], "uploader_id": user["id"],
                                         "status": "pending", "storage_key": photo_key, "thumbnail_key": thumb_key})
            await repo.create("revisions", {"recipe_id": recipe["id"], "actor_id": user["id"],
                                            "revision": 1, "content": {"directions": "exact prose\n\n"}})
            await repo.create("audit", {"action": "restore_test", "actor_id": user["id"]})
            await repo.create("jobs", {"recipe_id": recipe["id"], "dedupe_key": "restore-job"})
            await repo.create("pairings", {"source_user_id": user["id"], "status": "pending"})
            await repo.create("admin_sessions", {"secret_hash": "test-admin-digest"})
            await repo.create("usage", {"scope": user["id"], "kind": "upload", "count": 3})
            return {table: await repo.list(table) for table in repo_tables}

    async def inspect():
        async with Repository(destination) as repo:
            # No migration here: the supported export must contain schema and migration history.
            found = {table: await repo.list(table) for table in repo_tables}
            assert await repo.migrate() == []
            return found

    async def remove(setting):
        async with Repository(setting) as repo:
            await repo._connection.query(f"REMOVE DATABASE IF EXISTS `{setting.db_database}`;")

    repo_tables = list(MODELS)
    try:
        original = asyncio.run(seed())
        archive = tmp_path / "real.tar.gz"
        cli.backup(settings, archive, quiesced=True, binary=binary)
        cli.restore(destination, archive, quiesced=True, trusted=True, binary=binary)
        assert asyncio.run(inspect()) == original
        assert (destination.media_root / photo_key).read_bytes() == b"original test image"
        assert (destination.media_root / thumb_key).read_bytes() == b"test thumbnail"
        with pytest.raises(ValueError, match="NEW database"):
            cli._require_fresh_database(destination)
    finally:
        asyncio.run(remove(settings))
        asyncio.run(remove(destination))
