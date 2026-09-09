"""Own a disposable real database/API, or run its explicit admin CLI commands."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

backend = Path(__file__).resolve().parents[2] / "recipe-creator-backend"
state = Path(os.environ["RECIPE_E2E_STATE"])
cli = ["uv", "run", "--project", str(backend), "python", "-m", "recipe_creator.cli"]


def stop(process):
    if process and process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


def main():
    if len(sys.argv) > 1:
        if sys.argv[1] not in {"admin-grant", "admin-revoke"}:
            raise ValueError("Only explicit admin permission commands are supported")
        env = json.loads(state.read_text())
        return subprocess.run(cli + sys.argv[1:], cwd=env["RECIPE_MEDIA_ROOT"], env=env).returncode

    # Never connect to an existing database or inherit application .env settings.
    api_port = int(os.environ.get("RECIPE_E2E_API_PORT", "2332"))
    with socket.socket() as probe:
        # Permit a fresh run after graceful shutdown leaves TIME_WAIT sockets.
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", api_port))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        db_port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{int(os.environ.get('RECIPE_E2E_PORT', '2772'))}"
    database = api = None
    with tempfile.TemporaryDirectory(prefix="recipe-e2e-") as root:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("RECIPE_", "SURREAL_"))}
        env.update({
            "RECIPE_DB_URL": f"http://127.0.0.1:{db_port}",
            "RECIPE_DB_USER": "root",
            "RECIPE_DB_PASSWORD": uuid4().hex,
            "RECIPE_DB_NAMESPACE": "recipe_e2e",
            "RECIPE_DB_DATABASE": f"browser_{uuid4().hex}",
            "RECIPE_MEDIA_ROOT": root,
            "RECIPE_SECURE_COOKIES": "false",
            "RECIPE_ALLOWED_ORIGINS": json.dumps([origin]),
            "RECIPE_PUBLIC_ORIGIN": origin,
            "RECIPE_SESSION_SECRET": uuid4().hex + uuid4().hex,
            "RECIPE_AI_API_KEY": "",
            # Exercise real parse-error handling without contacting an external provider.
            "RECIPE_AI_BASE_URL": f"http://127.0.0.1:{api_port}/e2e-unavailable-ai",
            "WEB_CONCURRENCY": "1",
        })
        try:
            database = subprocess.Popen([
                "surreal", "start", "memory", "--bind", f"127.0.0.1:{db_port}",
                "--no-banner", "--log", "warn",
            ], cwd=root, env={**env, "SURREAL_USER": "root", "SURREAL_PASS": env["RECIPE_DB_PASSWORD"]},
                start_new_session=True)
            for _ in range(100):
                if database.poll() is not None:
                    raise RuntimeError("Disposable SurrealDB exited before readiness")
                try:
                    with urlopen(f"{env['RECIPE_DB_URL']}/health", timeout=1):
                        break
                except (URLError, TimeoutError):
                    time.sleep(0.1)
            else:
                raise RuntimeError("Disposable SurrealDB did not become ready")
            subprocess.run(cli + ["migrate"], cwd=root, env=env, check=True)
            with open(state, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as output:
                json.dump(env, output)
            print(f"E2E database: {env['RECIPE_DB_URL']} recipe_e2e/{env['RECIPE_DB_DATABASE']}; media: {root}", flush=True)
            api = subprocess.Popen([
                "uv", "run", "--project", str(backend), "uvicorn", "recipe_creator.app:app",
                "--host", "127.0.0.1", "--port", str(api_port), "--workers", "1", "--no-proxy-headers",
            ], cwd=root, env=env, start_new_session=True)
            return api.wait()
        finally:
            stop(api)
            stop(database)
            state.unlink(missing_ok=True)


def shutdown(signum, frame):
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    sys.exit(main())
