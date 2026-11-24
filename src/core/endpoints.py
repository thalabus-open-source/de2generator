"""Runtime-mutable upstream (vLLM/LiteLLM) base URL.

``VLLM_BASE_URL`` is resolved at import time from env/config (see
``core.constants``), but two things can change it while the process runs:

* ``--vllm-url`` on the command line (handled in ``main()``)
* a per-request ``de2generator.model_endpoint`` override (never persisted here)

Keeping the value behind accessors instead of a module-level global lets the
routes, the supervisor and the tests share one authoritative value.
"""

from typing import Optional

from .constants import VLLM_BASE_URL

_BASE_URL: str = VLLM_BASE_URL


def get_vllm_base_url() -> str:
    """Current default backend URL (env/config/CLI overridden value)."""
    return _BASE_URL


def set_vllm_base_url(url: str) -> None:
    """Override the default backend URL for the lifetime of the process."""
    global _BASE_URL
    _BASE_URL = url


def normalize_endpoint(url: str) -> str:
    """Strip trailing slashes and a trailing ``/v1[/chat/completions]``.

    The client sometimes sends a full completions URL as ``model_endpoint``.
    ``stream_from_vllm`` appends ``/v1/chat/completions`` itself, so a caller
    supplied suffix would produce a doubled path.
    """
    endpoint = url.rstrip("/")
    for suffix in ("/v1/chat/completions", "/v1"):
        if endpoint.endswith(suffix):
            endpoint = endpoint[: -len(suffix)]
            break
    return endpoint


def resolve_endpoint(override: Optional[str]) -> str:
    """Backend URL for a single request: ``override`` when given, else the default."""
    if not override:
        return get_vllm_base_url()
    return normalize_endpoint(override)
