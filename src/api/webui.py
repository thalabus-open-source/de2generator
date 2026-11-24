"""Web dashboard: static assets, live SSE stream and runtime toggles.

The dashboard itself is plain ES6 in ``src/static`` (no build step); everything
here either serves those files or exposes the small amount of state the UI can
change at runtime (logprobs pill, translation target).
"""

import os

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from core.config import delete_config_value, get_config_snapshot, set_config_value
from core.constants import SEMANTIC_LLM_ENABLED, UI_ENABLED, UI_SHOW_CONTENT
from ui import (
    ui_events_endpoint,
    ui_get_logprobs_state,
    ui_set_logprobs_enabled,
)
from utils import get_logger

from .paths import STATIC_DIR

log = get_logger()
router = APIRouter()

_NO_STORE = {"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"}


def _static(*parts: str) -> str:
    return os.path.join(STATIC_DIR, *parts)


# ── static assets ────────────────────────────────────────────────────────────

@router.get("/favicon.svg")
async def serve_favicon() -> FileResponse:
    return FileResponse(_static("icons", "favicon.svg"), media_type="image/svg+xml")


@router.get("/ui")
async def serve_ui() -> FileResponse:
    return FileResponse(_static("ui.html"), media_type="text/html")


@router.get("/ui/style.css")
async def serve_css() -> FileResponse:
    return FileResponse(_static("styles", "ui.css"), media_type="text/css", headers=_NO_STORE)


@router.get("/ui/app.js")
async def serve_js() -> FileResponse:
    return FileResponse(
        _static("scripts", "main.js"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/ui/scripts/{path:path}", response_model=None)
async def serve_js_modules(path: str) -> FileResponse | JSONResponse:
    """Serve JavaScript ES6 modules."""
    # Reject any traversal attempt before touching the filesystem
    if ".." in path or path.startswith("/"):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    js_file = _static("scripts", path)
    if not os.path.isfile(js_file):
        return JSONResponse(status_code=404, content={"error": "Script not found"})
    return FileResponse(
        js_file,
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )


# ── configuration and toggles ────────────────────────────────────────────────

@router.get("/ui/config.json")
async def serve_config() -> Response:
    """Serve the configuration the frontend needs (feature flags win over config.json)."""
    config_response = get_config_snapshot()
    config_response.setdefault("semantic_llm", {})["enabled"] = SEMANTIC_LLM_ENABLED
    config_response.setdefault("ui", {})["enabled"] = UI_ENABLED
    config_response["ui"]["show_content"] = UI_SHOW_CONTENT
    # Reflect the current global logprobs toggle so a page reload restores the pill
    config_response["logprobs"] = ui_get_logprobs_state()
    return JSONResponse(config_response)


@router.get("/ui/logprobs")
async def get_logprobs_state() -> JSONResponse:
    """Return the current logprobs toggle state."""
    return JSONResponse(ui_get_logprobs_state())


@router.post("/ui/logprobs")
async def set_logprobs_state(request: Request) -> JSONResponse:
    """Enable or disable the global logprobs feature (never persisted: defaults to off)."""
    try:
        data = await request.json()
    except Exception:
        data = {}
    state = ui_set_logprobs_enabled(bool(data.get("enabled")))
    log.info(f"[UI] Logprobs toggle -> {'enabled' if state['enabled'] else 'disabled'}")
    return JSONResponse(state)


@router.post("/ui/update-translation")
async def update_translation(request: Request) -> Response:
    """Update the runtime translation configuration (in-memory only)."""
    try:
        data = await request.json()
        enabled = data.get("enabled", False)
        language = data.get("language", None)
        log.info(f"[UI] Translation update request: enabled={enabled}, language={language}")

        set_config_value("translation.enabled", bool(enabled))
        # Language is stored as ui_language so the agent allow-list still applies —
        # only the agents listed in translation.thalabus.enable_only_for_agents translate.
        if enabled and language:
            set_config_value("translation.thalabus.ui_language", language)
            log.info(f"[UI] Translation enabled for {language}")
        else:
            delete_config_value("translation.thalabus.ui_language")
            log.info("[UI] Translation disabled")

        return JSONResponse({"status": "ok"})
    except Exception as exc:
        log.error(f"Error updating translation config: {exc}")
        return JSONResponse({"status": "error", "message": str(exc)}, status_code=400)


# ── live event stream ────────────────────────────────────────────────────────

@router.get("/ui/events")
async def ui_events(request: Request) -> StreamingResponse:
    """SSE endpoint for UI event streaming."""
    return await ui_events_endpoint(request)
