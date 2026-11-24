"""UI event broadcasting and SSE connection management."""

import asyncio
import json
import os
import time
from collections import deque
from typing import Optional

from fastapi import Request
from fastapi.responses import StreamingResponse

from core.constants import UI_MAX_HISTORY, UI_SHOW_CONTENT
from utils import get_logger

log = get_logger()


# UI state management
_ui_subscribers: list = []  # list[asyncio.Queue]
_ui_interactions: deque = deque(maxlen=UI_MAX_HISTORY)

# Logprobs feature state (UI toggle; default disabled)
_logprobs_state = {"enabled": False, "unsupported": False}

# ── Token accounting ─────────────────────────────────────────────────────────
# Session totals (reset via the UI "clear" button) and hourly usage buckets
# kept for the latest HOURLY_WINDOW_HOURS hours. Both are persisted to disk so
# they survive a proxy restart.
HOURLY_WINDOW_HOURS = 240
_token_totals: dict = {"input": 0, "reasoning": 0, "answer": 0, "requests": 0}
_hourly_usage: dict = {}  # hour epoch -> {input, reasoning, answer, requests}
_stats_file = os.path.join(os.getcwd(), "logs", "hourly_token_stats.json")


def _save_token_stats() -> None:
    """Persist totals + hourly buckets to disk (best effort)."""
    try:
        os.makedirs(os.path.dirname(_stats_file), exist_ok=True)
        payload = {
            "totals": _token_totals,
            "hours": {str(h): b for h, b in _hourly_usage.items()},
        }
        tmp = _stats_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)
        os.replace(tmp, _stats_file)
    except Exception as exc:
        log.warning(f"[UI] Could not persist token stats: {exc}")


def _load_token_stats() -> None:
    """Restore totals + hourly buckets from disk on startup."""
    try:
        with open(_stats_file, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        totals = payload.get("totals") or {}
        for key in ("input", "reasoning", "answer", "requests"):
            _token_totals[key] = int(totals.get(key) or 0)
        for hour, bucket in (payload.get("hours") or {}).items():
            try:
                _hourly_usage[int(hour)] = {
                    "input":    int(bucket.get("input") or 0),
                    "reasoning": int(bucket.get("reasoning") or 0),
                    "answer":   int(bucket.get("answer") or 0),
                    "requests": int(bucket.get("requests") or 0),
                }
            except (TypeError, ValueError):
                continue
        _prune_hourly_usage()
    except FileNotFoundError:
        pass
    except Exception as exc:
        log.warning(f"[UI] Could not load persisted token stats: {exc}")


def _prune_hourly_usage() -> None:
    """Drop hourly buckets older than the retention window."""
    min_hour = int(time.time() // 3600) - HOURLY_WINDOW_HOURS + 1
    for hour in [h for h in _hourly_usage if h < min_hour]:
        del _hourly_usage[hour]


def ui_record_token_usage(stats: dict) -> None:
    """Accumulate a completed interaction's token stats into totals and hourly buckets."""
    inp = int(stats.get("input_tokens") or 0)
    rea = int(stats.get("reasoning_tokens_est") or 0)
    ans = int(stats.get("answer_tokens_est") or 0)
    if not (inp or rea or ans):
        return
    hour = int(time.time() // 3600)
    bucket = _hourly_usage.setdefault(hour, {"input": 0, "reasoning": 0, "answer": 0, "requests": 0})
    bucket["input"] += inp
    bucket["reasoning"] += rea
    bucket["answer"] += ans
    bucket["requests"] += 1
    _token_totals["input"] += inp
    _token_totals["reasoning"] += rea
    _token_totals["answer"] += ans
    _token_totals["requests"] += 1
    _prune_hourly_usage()
    _save_token_stats()
    _ui_broadcast({"type": "token_totals", "totals": dict(_token_totals)})


def ui_get_token_totals() -> dict:
    """Return the accumulated token totals (copy)."""
    return dict(_token_totals)


def ui_reset_token_totals() -> dict:
    """Zero the accumulated totals (hourly history is kept) and broadcast."""
    for key in _token_totals:
        _token_totals[key] = 0
    _save_token_stats()
    _ui_broadcast({"type": "token_totals", "totals": dict(_token_totals)})
    return dict(_token_totals)


def ui_get_hourly_usage() -> dict:
    """Return hourly usage buckets (oldest first) plus totals, for the stats page."""
    _prune_hourly_usage()
    hours = [
        {"hour": h, **_hourly_usage[h]}
        for h in sorted(_hourly_usage)
    ]
    window_totals = {
        "input":    sum(b["input"] for b in hours),
        "reasoning": sum(b["reasoning"] for b in hours),
        "answer":   sum(b["answer"] for b in hours),
        "requests": sum(b["requests"] for b in hours),
    }
    return {
        "window_hours": HOURLY_WINDOW_HOURS,
        "hours": hours,
        "totals": dict(_token_totals),
        "window_totals": window_totals,
    }


def ui_update_token_counts(
    request_id: str,
    input_tokens: int,
    total_output_tokens: int,
    reasoning_tokens_est: int,
    answer_tokens_est: int,
) -> None:
    """Broadcast live input/estimated-reasoning/estimated-answer counts for a live request."""
    interaction = _ui_find(request_id)
    if interaction is None:
        return
    patch = {
        "input_tokens":        int(input_tokens or 0),
        "total_output_tokens": int(total_output_tokens or 0),
        "reasoning_tokens_est": int(reasoning_tokens_est or 0),
        "answer_tokens_est":   int(answer_tokens_est or 0),
    }
    stats = interaction.get("stats") or {}
    if all(stats.get(k) == v for k, v in patch.items()):
        return
    ui_update_stats(request_id, patch)


_load_token_stats()


def ui_get_logprobs_state() -> dict:
    """Return the current logprobs toggle state (copy)."""
    return dict(_logprobs_state)


def ui_logprobs_requested() -> bool:
    """True when outbound requests should ask the LLM for logprobs.

    Disabled while the LLM is known not to support them; re-enabling the
    toggle clears that flag and gives the backend another chance.
    """
    return bool(_logprobs_state["enabled"] and not _logprobs_state["unsupported"])


def ui_set_logprobs_enabled(enabled: bool) -> dict:
    """Set the global logprobs toggle and broadcast the new state to all UI clients."""
    _logprobs_state["enabled"] = bool(enabled)
    if enabled:
        # A fresh enable gives the LLM another chance to prove support.
        _logprobs_state["unsupported"] = False
    _ui_broadcast({"type": "logprobs_setting", **dict(_logprobs_state)})
    return dict(_logprobs_state)


def ui_logprobs_unsupported() -> None:
    """Mark that the LLM rejected/ignored logprobs and broadcast it (toggle turns red)."""
    if _logprobs_state["unsupported"]:
        return
    _logprobs_state["unsupported"] = True
    _ui_broadcast({"type": "logprobs_unsupported", **dict(_logprobs_state)})


def ui_logprobs_supported() -> None:
    """Clear a previous unsupported flag after a request served logprobs fine."""
    if not _logprobs_state["unsupported"]:
        return
    _logprobs_state["unsupported"] = False
    _ui_broadcast({"type": "logprobs_setting", **dict(_logprobs_state)})


def _hide_content_if_needed(text: str) -> str:
    """Replace content with placeholder if UI_SHOW_CONTENT is False."""
    if not UI_SHOW_CONTENT and text:
        return "[...] OMITTED [...]"
    return text


def _ui_broadcast(event: dict) -> None:
    """Broadcast an event to all connected UI clients via SSE."""
    dead = []
    for q in _ui_subscribers:
        try:
            q.put_nowait(event)
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            _ui_subscribers.remove(q)
        except ValueError:
            pass


def _ui_find(request_id: str) -> Optional[dict]:
    """Find an interaction by request_id."""
    for item in _ui_interactions:
        if item["id"] == request_id:
            return item
    return None


def ui_start_interaction(
    request_id: str,
    model: str,
    thinking_budget: Optional[int] = None,
    enable_thinking: bool = True,
    model_parameters: Optional[dict] = None,
    agent_name: Optional[str] = None,
    goal_id: Optional[str] = None,
    client_request: Optional[list] = None
) -> None:
    """Start a new interaction and broadcast to UI."""
    interaction_number = len(_ui_interactions) + 1
    interaction = {
        "id":             request_id,
        "number":         interaction_number,
        "model":          model,
        "thinking_budget": thinking_budget,
        "enable_thinking": enable_thinking,
        "thinking_tokens": 0,  # Track actual reasoning tokens from vLLM
        "status":         "in_progress",
        "checkpoints":    [],
        "final_content":  "",
        "final_reasoning": "",
        "stats":          {},
        "model_parameters": model_parameters or {},
        "client_request": client_request or [],
        "agent_name":     agent_name,  # Agent name for transaction card header
        "goal_id":        goal_id,     # Goal ID chain for transaction card header
        "started_at":     time.time(),
        "completed_at":   None,
    }
    _ui_interactions.append(interaction)
    _ui_broadcast({"type": "interaction_start", "interaction": dict(interaction, checkpoints=[])})


def ui_add_checkpoint(
    request_id: str,
    kind: str,
    label: str,
    text: str,
    entropy: Optional[float] = None,
    token_lps: Optional[list] = None
) -> None:
    """Add a checkpoint to an interaction and broadcast to UI."""
    interaction = _ui_find(request_id)
    if interaction is None:
        return
    # Store original text internally, but send hidden text to UI if configured
    displayed_text = _hide_content_if_needed(text)
    cp = {"kind": kind, "label": label, "text": displayed_text, "time": time.time(), "entropy": entropy}
    if token_lps:
        cp["token_lps"] = token_lps
    interaction["checkpoints"].append(cp)
    _ui_broadcast({"type": "checkpoint", "request_id": request_id, "checkpoint": cp})
    # If this is a rejection, also broadcast updated rejection count
    if kind == "reject":
        rejection_count = sum(1 for c in interaction["checkpoints"] if c["kind"] == "reject")
        ui_update_stats(request_id, {"rejected_checkpoints_count": rejection_count})


def ui_update_checkpoint_semantic(
    request_id: str,
    cp_idx: int,
    verdict: str,
    reason: str = "",
    full_response: str = ""
) -> None:
    """Update a checkpoint's semantic validation result and broadcast to UI."""
    interaction = _ui_find(request_id)
    if interaction is None:
        return
    if 0 <= cp_idx < len(interaction["checkpoints"]):
        interaction["checkpoints"][cp_idx]["semantic_verdict"] = verdict
        if reason:
            interaction["checkpoints"][cp_idx]["semantic_reason"] = reason
        if full_response:
            interaction["checkpoints"][cp_idx]["semantic_full_response"] = full_response
    _ui_broadcast({
        "type": "checkpoint_semantic_update",
        "request_id": request_id,
        "cp_idx": cp_idx,
        "verdict": verdict,
        "reason": reason,
        "full_response": full_response,
    })


def ui_update_thinking_tokens(request_id: str, thinking_tokens: int) -> None:
    """Update actual reasoning_tokens during streaming from vLLM usage field."""
    interaction = _ui_find(request_id)
    if interaction is None:
        return
    if thinking_tokens > interaction.get("thinking_tokens", 0):
        interaction["thinking_tokens"] = thinking_tokens
        # Update stats dict to trigger UI refresh
        ui_update_stats(request_id, {"thinking_tokens": thinking_tokens})


def ui_update_ttft(request_id: str, ttft_seconds: float) -> None:
    """Publish time-to-first-token once the LLM emits its first token."""
    interaction = _ui_find(request_id)
    if interaction is None:
        return
    interaction["ttft_seconds"] = ttft_seconds
    ui_update_stats(request_id, {"ttft_seconds": ttft_seconds})


def ui_update_stats(request_id: str, stats: dict) -> None:
    """Update partial stats during streaming (e.g., rejection count)."""
    interaction = _ui_find(request_id)
    if interaction is None:
        return
    interaction["stats"].update(stats)
    _ui_broadcast({"type": "stats_update", "request_id": request_id, "stats": interaction["stats"]})


def ui_update_checkpoint_translation(
    request_id: str,
    cp_idx: int,
    translated_text: str,
    language: str,
    elapsed_ms: int = 0,
) -> None:
    """Update a checkpoint's text to the translated version and broadcast to UI."""
    interaction = _ui_find(request_id)
    if interaction is None:
        return
    if 0 <= cp_idx < len(interaction["checkpoints"]):
        # Preserve original text in case UI wants to show both
        cp = interaction["checkpoints"][cp_idx]
        if "original_text" not in cp:
            cp["original_text"] = cp.get("text", "")
        cp["text"] = _hide_content_if_needed(translated_text)
        cp["translated"] = True
        cp["translation_language"] = language
        cp["translation_elapsed_ms"] = elapsed_ms
    _ui_broadcast({
        "type": "checkpoint_translation_update",
        "request_id": request_id,
        "cp_idx": cp_idx,
        "translated_text": _hide_content_if_needed(translated_text),
        "language": language,
        "elapsed_ms": elapsed_ms,
    })


def ui_complete_interaction(
    request_id: str,
    final_content: str,
    final_reasoning: str,
    stats: Optional[dict] = None,
    log_file_path: Optional[str] = None
) -> None:
    """Mark an interaction as complete and broadcast to UI."""
    interaction = _ui_find(request_id)
    if interaction is None:
        return
    # Hide content if configured
    displayed_content = _hide_content_if_needed(final_content or "")
    displayed_reasoning = _hide_content_if_needed(final_reasoning or "")
    
    interaction["status"] = "complete"
    interaction["final_content"] = displayed_content
    interaction["final_reasoning"] = displayed_reasoning
    interaction["stats"] = stats or {}
    interaction["log_file_path"] = log_file_path or ""  # Add log file path for frontend
    interaction["completed_at"] = time.time()
    ui_record_token_usage(interaction["stats"])
    _ui_broadcast({
        "type": "interaction_complete",
        "request_id": request_id,
        "status": "complete",
        "final_content": interaction["final_content"],
        "final_reasoning": interaction["final_reasoning"],
        "stats": interaction["stats"],
        "log_file_path": interaction["log_file_path"]
    })


def ui_cancel_interaction(request_id: str) -> None:
    """Mark an interaction as cancelled and broadcast its terminal state."""
    interaction = _ui_find(request_id)
    if interaction is None:
        return

    interaction["status"] = "cancelled"
    interaction["stats"] = {**interaction.get("stats", {}), "status": "cancelled"}
    interaction["completed_at"] = time.time()
    _ui_broadcast({
        "type": "interaction_complete",
        "request_id": request_id,
        "status": "cancelled",
        "final_content": interaction["final_content"],
        "final_reasoning": interaction["final_reasoning"],
        "stats": interaction["stats"],
        "log_file_path": interaction.get("log_file_path", ""),
    })


def ui_fail_interaction(request_id: str, message: str) -> None:
    """Mark an interaction as failed (backend error/timeout) so its card stops showing as live."""
    interaction = _ui_find(request_id)
    if interaction is None or interaction.get("status") != "in_progress":
        return
    ui_add_checkpoint(request_id, "error", message, "")
    interaction["status"] = "error"
    interaction["stats"] = {**interaction.get("stats", {}), "status": "error", "error": message}
    interaction["completed_at"] = time.time()
    _ui_broadcast({
        "type": "interaction_complete",
        "request_id": request_id,
        "status": "error",
        "final_content": interaction.get("final_content", ""),
        "final_reasoning": interaction.get("final_reasoning", ""),
        "stats": interaction["stats"],
        "log_file_path": interaction.get("log_file_path", ""),
    })


async def ui_events_endpoint(request: Request) -> StreamingResponse:
    """SSE endpoint for UI event streaming.
    
    This endpoint is mounted at /ui/events and provides Server-Sent Events
    for real-time updates to the web UI.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _ui_subscribers.append(q)

    async def event_stream():
        try:
            # Send full history on connection
            history = [dict(i, checkpoints=list(i["checkpoints"])) for i in _ui_interactions]
            yield f"data: {json.dumps({'type': 'history', 'interactions': history})}\n\n"
            # Send the accumulated token totals so the header pill restores itself
            yield f"data: {json.dumps({'type': 'token_totals', 'totals': dict(_token_totals)})}\n\n"
            
            # Stream new events
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30.0)
                    if event.get("type") == "shutdown":
                        break
                    yield f"data: {json.dumps(event)}\n\n"

                except asyncio.TimeoutError:
                    # Send ping to keep connection alive
                    yield ": ping\n\n"

        finally:
            try:
                _ui_subscribers.remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
