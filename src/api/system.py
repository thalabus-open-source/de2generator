"""Model discovery and health endpoints.

Three questions, three endpoints, deliberately not merged:

* ``/health`` — "should this container be restarted?" Liveness only, no upstream
  traffic, never fails while the process serves. The healthcheck probes this.
* ``/health/upstream`` — "is the backend usable from here?" Diagnostic only: its
  verdict never propagates into a health status, it just names the component
  that is failing.
* ``/v1/models`` — the real proxy, the path client traffic actually depends on.
"""

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from core.endpoints import get_vllm_base_url

router = APIRouter()

MODEL_LIST_TIMEOUT_SECONDS = 10.0
UPSTREAM_PROBE_TIMEOUT_SECONDS = 4.0


@router.get("/health/upstream")
async def health_upstream() -> dict:
    """Report whether the configured backend answers, without acting on it.

    Any HTTP response counts as reachable: a backend that is up but demanding
    credentials (401/403) is a configuration finding, not an outage, and
    conflating the two is how a status badge stops meaning anything. This never
    raises and never gates a container — read it from the dashboard or from an
    alert rule that knows the difference.
    """
    base_url = get_vllm_base_url()
    probe: dict = {"base_url": base_url, "reachable": False,
                   "status_code": None, "error": None}
    try:
        async with httpx.AsyncClient(timeout=UPSTREAM_PROBE_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{base_url}/v1/models")
        probe["reachable"] = True
        probe["status_code"] = resp.status_code
    except Exception as exc:                        # report, never fail
        probe["error"] = f"{type(exc).__name__}: {exc}"
    return probe


@router.get("/v1/models")
async def list_models() -> JSONResponse:
    """Proxy the model list from the backend."""
    try:
        async with httpx.AsyncClient(timeout=MODEL_LIST_TIMEOUT_SECONDS) as client:
            resp = await client.get(f"{get_vllm_base_url()}/v1/models")
        return JSONResponse(content=resp.json(), status_code=resp.status_code)
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": {"message": str(exc)}})


@router.get("/health")
async def health() -> dict:
    """Liveness only: says nothing about the backend, see ``/health/upstream``."""
    return {"status": "ok", "component": "de2generator"}
