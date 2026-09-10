"""Run Uvicorn with Logly's Uvicorn logging integration."""

import argparse

import uvicorn
from logly.integrations.uvicorn import setup_uvicorn_logging


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser()
    command.add_argument("--host", default="127.0.0.1")
    command.add_argument("--port", type=int, default=8000)
    command.add_argument("--reload", action="store_true")
    command.add_argument("--reload-dir", action="append", dest="reload_dirs")
    command.add_argument("--no-proxy-headers", action="store_false", dest="proxy_headers")
    command.set_defaults(proxy_headers=True)
    return command


def main() -> None:
    args = parser().parse_args()
    # None tells the integration to use Logly's native colored console format.
    setup_uvicorn_logging(format=None)
    uvicorn.run("recipe_creator.app:create_app", factory=True, host=args.host, port=args.port,
                reload=args.reload, reload_dirs=args.reload_dirs, proxy_headers=args.proxy_headers,
                log_config=None)


if __name__ == "__main__":
    main()
