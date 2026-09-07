"""Explicit operator commands. Configuration is read from RECIPE_* / private .env."""
import argparse
import asyncio
from datetime import UTC, datetime
import getpass
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit, urlunsplit

import httpx

from .legacy import import_recipes, load_snapshot
from .repository import Repository
from .settings import Settings


SURREAL_VERSION = "3.2.4"
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 20 * 1024**3
MEDIA_NAME = re.compile(r"[0-9a-f]{32}(?:_thumb)?\.jpg(?:\.partial)?\Z")


def _no_symlinks(path: Path):
    path = path.absolute()
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError(f"Symlinks are not allowed: {part}")


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _versions() -> dict:
    return {
        "python": platform.python_version(),
        "packages": dict(sorted((dist.metadata["Name"].lower(), dist.version)
                                for dist in metadata.distributions() if dist.metadata["Name"])),
        "surrealdb": SURREAL_VERSION,
    }


def _endpoint(settings: Settings) -> str:
    parts = urlsplit(settings.db_url)
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    if scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
        raise ValueError("Database endpoint must be an HTTP(S)/WS(S) URL without credentials")
    return urlunsplit((scheme, parts.netloc, "", "", ""))


def _database_client(settings):
    return httpx.Client(base_url=_endpoint(settings), timeout=60, trust_env=False,
                        auth=(settings.db_user, settings.db_password.get_secret_value()),
                        headers={"Accept": "application/json", "Surreal-NS": settings.db_namespace,
                                 "Surreal-DB": settings.db_database})


def _sql_info(client, query):
    response = client.post("/sql", content=query, headers={"Content-Type": "text/plain"})
    response.raise_for_status()
    results = response.json()
    if not isinstance(results, list) or len(results) != 1 or results[0].get("status") != "OK":
        raise ValueError("Unable to verify database state; restore refused")
    return results[0]["result"]


def _require_fresh_database(settings):
    # Restore never merges into an existing database, even an apparently empty one.
    with _database_client(settings) as client:
        root = _sql_info(client, "INFO FOR ROOT;")
        if settings.db_namespace not in root.get("namespaces", {}):
            return
        namespace = _sql_info(client, "INFO FOR NS;")
        if settings.db_database in namespace.get("databases", {}):
            raise ValueError("Restore requires a NEW database name; existing database refused")


def _check_versions(settings, binary):
    result = subprocess.run([binary, "version"], capture_output=True, text=True, check=True, timeout=30)
    if result.stdout.strip().split("+")[0].split()[0] != SURREAL_VERSION:
        raise ValueError(f"This release requires SurrealDB CLI {SURREAL_VERSION}")
    with _database_client(settings) as client:
        response = client.get("/version")
        response.raise_for_status()
        if not re.search(rf"(?:^|[-\s]){re.escape(SURREAL_VERSION)}(?:[+\s]|$)", response.text):
            raise ValueError(f"This release requires SurrealDB server {SURREAL_VERSION}")


def _surreal(settings, binary, operation, path):
    # Credentials are environment-only, never argv or archive contents. Do not inherit
    # unrelated SURREAL_* switches (token, log-file, auth-level, etc.) from a shell.
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "HOME", "TMPDIR", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
    env.update(SURREAL_USER=settings.db_user, SURREAL_PASS=settings.db_password.get_secret_value())
    command = [binary, operation, "--endpoint", _endpoint(settings), "--namespace", settings.db_namespace,
               "--database", settings.db_database, "--auth-level", "root", "--log", "none"]
    if operation == "export":
        # No database users/passwords, access definitions, parameters or server config.
        command.extend(["--only", "--tables=true", "--records=true"])
    command.append(str(path))
    result = subprocess.run(command, env=env, capture_output=True, timeout=3600)
    if result.returncode:
        raise ValueError(f"SurrealDB {operation} failed; output withheld to avoid disclosing credentials")


def backup(settings: Settings, archive: Path, *, quiesced=False, binary="surreal") -> dict:
    """Keep API, workers and other writers stopped until this command completes."""
    if not quiesced:
        raise ValueError("Stop API/workers/other writers, then pass --confirm-quiesced (DB stays running)")
    archive = archive.absolute()
    media = settings.media_root.absolute()
    _no_symlinks(archive)
    _no_symlinks(media)
    if archive.exists():
        raise ValueError("Backup archive already exists; refusing overwrite")
    if archive.is_relative_to(media):
        raise ValueError("Backup archive must be outside the media directory")
    if not media.is_dir():
        raise ValueError("Media root must exist, even if empty")
    _check_versions(settings, binary)
    with tempfile.TemporaryDirectory(prefix="recipe-backup-") as directory:
        stage = Path(directory)
        database = stage / "database.surql"
        _surreal(settings, binary, "export", database)
        if not database.is_file() or database.stat().st_size == 0:
            raise ValueError("Database export is empty or missing")
        (stage / "media").mkdir()
        for source in sorted(media.iterdir()):
            _no_symlinks(source)
            if not source.is_file() or not MEDIA_NAME.fullmatch(source.name):
                raise ValueError(f"Unexpected media entry (config/secrets are never archived): {source.name}")
            shutil.copyfile(source, stage / "media" / source.name)
        files = {}
        for source in sorted(stage.rglob("*")):
            if source.is_file():
                files[source.relative_to(stage).as_posix()] = {
                    "sha256": _checksum(source), "size": source.stat().st_size,
                }
        manifest = {"format": FORMAT_VERSION, "created_at": datetime.now(UTC).isoformat(),
                    "versions": _versions(), "database_format": "surrealql",
                    "consistency": "all application writers and workers quiesced by operator",
                    "media_complete": True, "files": files}
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        # Exclusive creation and owner-only permissions; remove partial archives on failure.
        descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                with tarfile.open(fileobj=stream, mode="w:gz") as tar:
                    for name in ["manifest.json", "database.surql", "media"]:
                        tar.add(stage / name, arcname=name)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
    return manifest


def _member_name(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or ".." in path.parts or "\\" in name
            or str(path) != name):
        raise ValueError("Unsafe archive path")
    if name not in {"manifest.json", "database.surql", "media"}:
        if len(path.parts) != 2 or path.parts[0] != "media" or not MEDIA_NAME.fullmatch(path.name):
            raise ValueError("Unexpected archive member")
    return name


def verify_archive(archive: Path, stage: Path) -> dict:
    """Validate every member and checksum BEFORE returning any importable data.

    Only regular files and the media directory are allowed. Never use extractall.
    A checksum is integrity, not authenticity: restore only a trusted operator backup.
    """
    _no_symlinks(archive)
    with tarfile.open(archive, "r:gz") as tar:
        members = {}
        total = 0
        for member in tar:
            name = _member_name(member.name)
            if name in members or not (member.isfile() or (name == "media" and member.isdir())):
                raise ValueError("Duplicate, symlink, hardlink or special archive member")
            members[name] = member
            total += member.size
            if total > MAX_ARCHIVE_BYTES or len(members) > 100_000:
                raise ValueError("Archive exceeds restore safety limits")
        manifest_member = members.get("manifest.json")
        if not manifest_member or not manifest_member.isfile() or manifest_member.size > 10 * 1024**2:
            raise ValueError("Missing or oversized manifest")
        with tar.extractfile(manifest_member) as stream:
            manifest = json.load(stream)
        if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT_VERSION
                or manifest.get("database_format") != "surrealql"
                or manifest.get("media_complete") is not True or not isinstance(manifest.get("files"), dict)):
            raise ValueError("Unsupported or incomplete backup manifest")
        files = manifest["files"]
        if "database.surql" not in files or "media" not in members or not members["media"].isdir():
            raise ValueError("Backup must include database and complete media directory")
        if set(members) != set(files) | {"manifest.json", "media"}:
            raise ValueError("Manifest does not match archive contents")
        for name, expected in files.items():
            _member_name(name)
            member = members[name]
            if (not member.isfile() or not isinstance(expected, dict)
                    or expected.get("size") != member.size
                    or not re.fullmatch(r"[a-f0-9]{64}", str(expected.get("sha256", "")))):
                raise ValueError("Invalid file manifest")
            digest = hashlib.sha256()
            with tar.extractfile(member) as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != expected["sha256"]:
                raise ValueError(f"Checksum mismatch: {name}")
        if manifest.get("versions") != _versions():
            raise ValueError("Pinned versions differ; restore with the original locked environment")
        # All verification completed before writing even staged data.
        (stage / "media").mkdir()
        for name in files:
            with tar.extractfile(members[name]) as source, (stage / name).open("xb") as destination:
                shutil.copyfileobj(source, destination)
            (stage / name).chmod(0o600)
    return manifest


def restore(settings: Settings, archive: Path, *, quiesced=False, trusted=False, binary="surreal") -> dict:
    if not quiesced or not trusted:
        raise ValueError("Restore requires --confirm-quiesced and --confirm-trusted-archive; "
                         "stop API/workers, and use a NEW database and absent media directory")
    media = settings.media_root.absolute()
    _no_symlinks(media)
    if media.exists() or not media.parent.is_dir():
        raise ValueError("Restore media destination must be absent, with an existing parent directory")
    # Same filesystem permits an atomic media rename after successful database import.
    with tempfile.TemporaryDirectory(prefix=".recipe-restore-", dir=media.parent) as directory:
        stage = Path(directory)
        manifest = verify_archive(archive, stage)
        _check_versions(settings, binary)
        _require_fresh_database(settings)
        try:
            _surreal(settings, binary, "import", stage / "database.surql")
            if media.exists():
                raise ValueError("Media destination appeared during restore")
            (stage / "media").rename(media)
        except BaseException as exc:
            raise ValueError("Restore incomplete: keep application offline. Database target may be partial; "
                             "discard it explicitly and retry to a NEW database/media destination") from exc
    return manifest


async def _database_command(args, settings):
    async with Repository(settings) as repo:
        if args.command == "migrate":
            return {"migrations": await repo.migrate()}
        if args.command == "import":
            report = await import_recipes(repo, load_snapshot(args.snapshot), dry_run=args.dry_run)
            return report.as_dict()
        if args.command == "cleanup":
            from .photos import PhotoService
            await PhotoService(repo, settings).cleanup()
            return {"ok": True, "scope": "photo retention, interrupted uploads and media orphans"}


def parser():
    result = argparse.ArgumentParser(
        description="Recipe Creator maintenance (RECIPE_* environment/private .env; no deployment automation).",
        epilog="Examples: python -m recipe_creator.cli import snapshot.json --dry-run; "
               "python -m recipe_creator.cli backup backup.tar.gz --confirm-quiesced. "
               "DB must remain running during backup/restore; stop API, workers and other writers. "
               "Keep backups private/off-device. Back up secrets/config separately.")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="Apply versioned migrations; back up and stop writers first")
    importer = commands.add_parser("import", help="Import an explicit prototype JSON list or recipes wrapper")
    importer.add_argument("snapshot", type=Path)
    importer.add_argument("--dry-run", action="store_true", help="Validate and report duplicates/conflicts, without writes")
    for name in ("backup", "restore"):
        command = commands.add_parser(name, help=("Export DB + media to a private checksummed archive" if name == "backup"
                                                  else "Verify and restore trusted backup into NEW DB and absent media root"))
        command.add_argument("archive", type=Path)
        command.add_argument("--confirm-quiesced", action="store_true", required=True,
                             help="I stopped API/workers and ALL other writers; DB itself is running")
        command.add_argument("--surreal-binary", default="surreal", help=f"SurrealDB {SURREAL_VERSION} executable")
        if name == "restore":
            command.add_argument("--confirm-trusted-archive", action="store_true", required=True,
                                 help="I trust this SQL archive; checksums are not authentication. "
                                      "Set RECIPE_DB_DATABASE to a NEW name and RECIPE_MEDIA_ROOT to an absent path")
    commands.add_parser("cleanup", help="Reconcile photo retention, interrupted uploads and orphan media")
    commands.add_parser("hash-password", help="Prompt twice for an admin password and print its Argon2id hash")
    commands.add_parser("export-openapi", help="Print OpenAPI JSON without connecting to DB or starting workers")
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "hash-password":
            from argon2 import PasswordHasher
            password = getpass.getpass("Admin password: ")
            if len(password) < 12:
                raise ValueError("Use at least 12 characters")
            if getpass.getpass("Confirm password: ") != password:
                raise ValueError("Passwords do not match")
            print(PasswordHasher().hash(password))
            return 0
        if args.command == "export-openapi":
            from .app import create_app
            print(json.dumps(create_app().openapi(), indent=2))
            return 0
        settings = Settings()
        if args.command == "backup":
            output = backup(settings, args.archive, quiesced=args.confirm_quiesced, binary=args.surreal_binary)
        elif args.command == "restore":
            output = restore(settings, args.archive, quiesced=args.confirm_quiesced,
                             trusted=args.confirm_trusted_archive, binary=args.surreal_binary)
        else:
            output = asyncio.run(_database_command(args, settings))
        print(json.dumps(output, indent=2, default=str))
        return 0 if output.get("ok", True) else 2
    except (ValueError, OSError, tarfile.TarError, httpx.HTTPError, subprocess.SubprocessError) as exc:
        # HTTP/subprocess exception text may contain request URLs or credentials.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(f"error: {message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
