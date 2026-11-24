"""
de²generator — Generation Supervisor

A transparent OpenAI-compatible proxy between thalabus and a vLLM backend.
Detects generation degeneration in real time, checkpoints healthy output, and
recovers automatically via continuation or no-think retry.

This module is the *entry point* only: it configures logging, mounts the routers
in :mod:`api` and starts uvicorn.  The responsibilities live in focused packages:

============================  ==================================================
``api``                       HTTP endpoints (chat, models, dashboard, stats)
``orchestration``             request parsing, the supervision state machine,
                              cancellation, telemetry
``response``                  OpenAI-compatible response assembly
``streaming``                 upstream streaming client
``detection`` / ``recovery``  detectors, checkpoint policy, retry strategies
``translation``               optional answer translation
``ui``                        SSE event bus and dashboard state
``core``                      configuration, constants, data models
============================  ==================================================

For programmatic use::

    from de2generator import app                       # ASGI application
    from orchestration import supervise_generation     # run one generation

Note that tests which need to replace the supervisor must patch it where it is
*used* — ``api.chat.supervise_generation`` — not this façade.
"""

import argparse
import asyncio
import os
import signal
import sys

import uvicorn
from fastapi import FastAPI

from api import ROUTERS
from core import set_vllm_base_url
from core.constants import (
    LISTEN_HOST,
    LISTEN_PORT,
    SERVER_PROTOCOL,
    SERVER_SSL_CERT_PATH,
    SERVER_SSL_KEY_PATH,
)
from utils import get_logger, setup_logging

setup_logging()
log = get_logger()

APP_TITLE = "de²generator"
APP_VERSION = "1.0.0"


def create_app() -> FastAPI:
    """Build the ASGI application with every router mounted."""
    application = FastAPI(title=APP_TITLE, version=APP_VERSION)
    for router in ROUTERS:
        application.include_router(router)
    return application


app = create_app()


def _ssl_config() -> dict:
    """uvicorn SSL kwargs, or an empty dict for plain HTTP.

    Returns ``None`` when HTTPS is configured but the material is missing, so the
    caller can refuse to start instead of silently serving the wrong protocol.
    """
    if SERVER_PROTOCOL.lower() != "https":
        return {}
    if not SERVER_SSL_CERT_PATH or not SERVER_SSL_KEY_PATH:
        log.error("HTTPS protocol selected but SSL certificate/key paths not configured")
        return None
    for path, label in ((SERVER_SSL_CERT_PATH, "certificate"), (SERVER_SSL_KEY_PATH, "key")):
        if not os.path.exists(path):
            log.error(f"SSL {label} not found: {path}")
            return None
    log.info(f"HTTPS enabled with certificate: {SERVER_SSL_CERT_PATH}")
    return {"ssl_certfile": SERVER_SSL_CERT_PATH, "ssl_keyfile": SERVER_SSL_KEY_PATH}


def main() -> int:
    parser = argparse.ArgumentParser(description="de²generator — Generation Supervisor")
    parser.add_argument("--vllm-url", type=str, default=None,
                        help="Override VLLM_BASE_URL (env var: VLLM_BASE_URL)")
    args = parser.parse_args()
    if args.vllm_url:
        set_vllm_base_url(args.vllm_url)
        log.info(f"VLLM_BASE_URL overridden to: {args.vllm_url}")

    ssl_config = _ssl_config()
    if ssl_config is None:
        return 1

    config = uvicorn.Config(
        app,
        host=str(LISTEN_HOST),
        port=int(LISTEN_PORT),
        log_level="info",
        access_log=True,
        **ssl_config,  # type: ignore[arg-type]
    )
    server = uvicorn.Server(config)
    # Disable uvicorn's own signal handling so our handler gets SIGINT first
    server.install_signal_handlers = False  # type: ignore[assignment]

    # Immediate exit: a supervising proxy must never linger after a stop signal,
    # because a half-closed stream would look like a healthy upstream to clients.
    def handle_signal(signum, frame):
        os._exit(0)

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        os._exit(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
