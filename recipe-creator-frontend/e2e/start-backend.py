"""Start a migrated, isolated API for Playwright against the local test SurrealDB."""
import json
import os
from pathlib import Path
import socket
import subprocess
from uuid import uuid4

backend = Path(__file__).resolve().parents[2] / "recipe-creator-backend"
origin = f"http://127.0.0.1:{int(os.environ.get('RECIPE_E2E_PORT', '5173'))}"
run_id = uuid4().hex

# Fail before migrating if another application owns the API port.
with socket.socket() as probe:
    probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    probe.bind(("127.0.0.1", 8080))

env = {
    **os.environ,
    "RECIPE_DB_URL": "http://127.0.0.1:18081",
    "RECIPE_DB_USER": "root",
    "RECIPE_DB_PASSWORD": "foundation-test-only",
    "RECIPE_DB_NAMESPACE": "recipe_e2e",
    "RECIPE_DB_DATABASE": f"browser_{run_id}",
    "RECIPE_MEDIA_ROOT": f"/tmp/recipe-e2e-media-{run_id}",
    "RECIPE_SECURE_COOKIES": "false",
    "RECIPE_ALLOWED_ORIGINS": json.dumps([origin]),
    "RECIPE_PUBLIC_ORIGIN": origin,
    "RECIPE_SESSION_SECRET": uuid4().hex + uuid4().hex,
    "RECIPE_AI_API_KEY": "",
    "RECIPE_E2E_ADMIN_PASSWORD": os.environ.get("RECIPE_E2E_ADMIN_PASSWORD", "recipe-e2e-test-only"),
}
env["RECIPE_ADMIN_PASSWORD_HASH"] = subprocess.check_output(
    ["uv", "run", "python", "-c",
     "import os; from argon2 import PasswordHasher; print(PasswordHasher().hash(os.environ['RECIPE_E2E_ADMIN_PASSWORD']))"],
    cwd=backend, env=env, text=True,
).strip()
subprocess.run(["uv", "run", "python", "-m", "recipe_creator.cli", "migrate"],
               cwd=backend, env=env, check=True)
print(f"E2E database: recipe_e2e/{env['RECIPE_DB_DATABASE']}; media: {env['RECIPE_MEDIA_ROOT']}", flush=True)
os.chdir(backend)
os.execvpe("uv", ["uv", "run", "uvicorn", "recipe_creator.app:app", "--host", "127.0.0.1",
                   "--port", "8080", "--no-proxy-headers"], env)
