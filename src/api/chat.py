"""Chat-completion and cancellation endpoints — the client-facing API.

This module is deliberately thin: parse the body, register the optional
cancellation token, hand the request to
:func:`orchestration.supervisor.supervise_generation`, then translate the outcome
into an HTTP response (plus the bookkeeping the UI and the audit log need).
All supervision policy lives in the supervisor.
"""

import json
import uuid

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core.endpoints import get_vllm_base_url
from core.models import GenerationSession
from orchestration import (
    RequestControl,
    cancel_request,
    extract_cancellation_token,
    register_request,
    run_cancellable_generation,
    unregister_request,
    validate_cancellation_token,
)
from orchestration.supervisor import supervise_generation
from streaming.vllm_client import BackendTimeoutError
from ui import ui_cancel_interaction, ui_complete_interaction, ui_fail_interaction
from utils import get_logger, log_llm_interaction

log = get_logger()
router = APIRouter()

# Returned when the client cancelled: 499 is nginx's "client closed request".
CLIENT_CANCELLED_STATUS = 499


@router.post("/v1/cancel")
async def cancel(request: Request) -> JSONResponse:
    """Abort an in-flight generation by cancellation token.  Idempotent."""
    try:
        body = await request.json()
        cancellation_token = validate_cancellation_token(body.get("cancellation_token"))
    except (AttributeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return JSONResponse(status_code=400, content={"error": {"message": str(exc)}})

    result = await cancel_request(cancellation_token)
    if result.control is None:
        return JSONResponse(content={"status": "not_found"})
    return JSONResponse(content={
        "status": result.status,
        "request_id": result.control.request_id,
    })


@router.post("/v1/chat/completions")
async def chat_completions(request: Request) -> JSONResponse:
    """OpenAI-compatible chat completion, supervised for degeneration."""
    try:
        body = await request.json()
        cancellation_token = extract_cancellation_token(body)
    except Exception as exc:
        return JSONResponse(status_code=400, content={"error": {"message": str(exc)}})

    request_id = str(uuid.uuid4())
    control: RequestControl | None = None
    if cancellation_token is not None:
        control = await register_request(cancellation_token, request_id)
        if control is None:
            return JSONResponse(
                status_code=409,
                content={"error": {"message": "cancellation_token is already in use"}},
            )

    try:
        generation_result = await _run_generation(body, request, request_id, control)
        if generation_result is None:
            ui_cancel_interaction(request_id)
            return JSONResponse(
                status_code=CLIENT_CANCELLED_STATUS,
                content={
                    "error": {
                        "message": "Generation cancelled by client request",
                        "type": "request_cancelled",
                        "code": "client_cancelled",
                    },
                    "de2generator": {"request_id": request_id, "status": "cancelled"},
                },
            )

        result, session = generation_result
        req_id = (result.get("de2generator") or {}).get("request_id", "")
        choices = result.get("choices") or []
        message = choices[0].get("message", {}) if choices else {}
        ui_complete_interaction(
            req_id,
            message.get("content", "") or "",
            message.get("reasoning_content", "") or "",
            stats=result.get("de2generator", {}),
            log_file_path=_interaction_log_path(session, message),
        )
        return JSONResponse(content=result)

    except httpx.ConnectError as exc:
        log.error(f"Cannot reach vLLM at {get_vllm_base_url()}: {exc}")
        ui_fail_interaction(request_id, f"cannot connect to backend: {exc}")
        return JSONResponse(
            status_code=502,
            content={"error": {"message": f"Cannot connect to vLLM backend: {exc}"}},
        )
    except (BackendTimeoutError, httpx.TimeoutException) as exc:
        detail = str(exc) or type(exc).__name__
        log.error(f"[{request_id}] Backend timeout: {detail}")
        ui_fail_interaction(request_id, f"backend timeout: {detail}")
        return JSONResponse(
            status_code=504,
            content={"error": {"message": f"Backend timeout: {detail}"}},
        )
    except Exception as exc:
        log.exception(f"Unhandled error: {exc}")
        ui_fail_interaction(request_id, f"error: {exc}")
        return JSONResponse(status_code=500, content={"error": {"message": str(exc)}})
    finally:
        if cancellation_token is not None and control is not None:
            await unregister_request(cancellation_token, control)


async def _run_generation(body: dict, request: Request, request_id: str,
                         control: "RequestControl | None"):
    """Run the generation, honouring the cancellation control when one exists.

    ``supervise_generation`` is looked up as a module global on every call, so a
    test can replace ``api.chat.supervise_generation`` and control the outcome.
    """
    if control is None:
        return await supervise_generation(body, request=request, request_id=request_id)
    return await run_cancellable_generation(
        supervise_generation, body, request, request_id, control
    )


def _interaction_log_path(session: GenerationSession, message: dict) -> str:
    """Persist the interaction when the model overthought, for later tuning.

    The *original* (untranslated) answer is logged together with the translation
    when there is one, so a regression can be reproduced from the log alone.
    """
    if not session.should_log_interaction:
        return ""

    original_content = "".join(
        cp.generated_text for cp in session.checkpoints
        if not (getattr(cp, "metrics_snapshot", None) or {}).get("thinking_checkpoint")
    ) or message.get("content", "")
    translated_note = ""
    if session.translation_language and session.translated_checkpoints:
        translated_note = (
            f"\n\n=== TRANSLATED ({session.translation_language}) ===\n"
            f"{message.get('content', '')}"
        )
    return log_llm_interaction(
        request_data=session.original_request,
        overthinking_text=session.thinking_text,
        final_answer=original_content + translated_note,
        request_id=session.request_id,
    ) or ""
