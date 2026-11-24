"""Token-usage statistics and interaction-log download.

``/logs/{filename}`` serves the files ``utils.logging.log_llm_interaction`` writes
(overthinking incidents captured for tuning).  The endpoint is deliberately
narrow: a fixed filename prefix, no subdirectories, and a canonical-path check.
"""

import os

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

from ui import ui_get_hourly_usage, ui_get_token_totals, ui_reset_token_totals
from utils import get_logger

from .paths import LOGS_DIR, STATIC_DIR

log = get_logger()
router = APIRouter()

LOG_FILE_PREFIX = "log_llm_interaction_"


@router.get("/ui/stats")
async def serve_stats_page() -> FileResponse:
    """Serve the hourly token statistics page."""
    return FileResponse(os.path.join(STATIC_DIR, "stats.html"), media_type="text/html")


@router.get("/ui/stats/hourly")
async def get_hourly_stats() -> JSONResponse:
    """Return hourly token usage statistics for the latest 240 hours."""
    return JSONResponse(ui_get_hourly_usage())


@router.get("/ui/stats/totals")
async def get_token_totals() -> JSONResponse:
    """Return accumulated token totals shown in the header pill."""
    return JSONResponse(ui_get_token_totals())


@router.post("/ui/stats/reset-totals")
async def reset_token_totals() -> JSONResponse:
    """Reset accumulated token totals (hourly history is kept)."""
    return JSONResponse(ui_reset_token_totals())


@router.get("/logs/{filename}")
async def read_log_file(filename: str) -> Response:
    """Download one LLM interaction log from the logs directory."""
    if not _is_allowed_log_file(filename):
        return JSONResponse(status_code=403, content={"error": "Access denied"})

    filepath = os.path.abspath(os.path.join(LOGS_DIR, filename))
    log_dir_abs = os.path.abspath(LOGS_DIR)
    if not filepath.startswith(log_dir_abs):
        return JSONResponse(status_code=403, content={"error": "Access denied"})
    if not os.path.isfile(filepath):
        return JSONResponse(status_code=404, content={"error": "Log file not found"})
    return FileResponse(filepath, media_type="text/plain", filename=filename)


def _is_allowed_log_file(filename: str) -> bool:
    """Only interaction logs, never anything with a path component."""
    if filename.startswith(LOG_FILE_PREFIX):
        return ".." not in filename and "/" not in filename
    return False
