"""vLLM streaming client for consuming SSE responses."""

import json
import math
import asyncio
from typing import Optional

import httpx

from core.constants import VLLM_PREFILL_TIMEOUT, VLLM_STALL_TIMEOUT
from utils import get_logger
from ui import ui_logprobs_unsupported, ui_logprobs_supported

log = get_logger()


class BackendTimeoutError(RuntimeError):
    """The backend produced no first token (prefill) or went silent mid-stream."""


# Grace period for the trailing usage chunk / [DONE] line after finish_reason.
_POST_FINISH_GRACE = 5.0


def _softmax_top(entry: dict) -> tuple[float, float, list]:
    """Return (probability, margin, top_candidates) for one logprobs entry.

    Servers started with `--logprobs-mode raw_logits` report raw (positive,
    unnormalised) logits in the `logprob` field; anything else is treated as a
    log-probability. The probability is renormalised over the returned candidate
    set only, so it is an upper bound of the true softmax probability.
    """
    cands = entry.get("top_logprobs") or []
    if not cands:
        return 1.0, 0.0, []

    logits = [float(c.get("logprob") or 0.0) for c in cands]
    token = entry.get("token")
    try:
        idx = next(i for i, c in enumerate(cands) if c.get("token") == token)
    except StopIteration:
        idx = 0

    top = max(logits)
    exps = [math.exp(v - top) for v in logits]
    total = sum(exps)
    prob = exps[idx] / total if total > 0 else 0.0
    others = [v for i, v in enumerate(logits) if i != idx]
    margin = logits[idx] - (max(others) if others else logits[idx])
    ranked = sorted(
        ((c.get("token") or "", float(c.get("logprob") or 0.0)) for c in cands),
        key=lambda kv: -kv[1],
    )[:4]
    return prob, margin, ranked


def _entry_to_compact(entry: dict) -> Optional[dict]:
    """Convert a raw logprobs entry into the compact form sent to the UI."""
    token = entry.get("token")
    if token is None:
        return None
    prob, margin, ranked = _softmax_top(entry)
    return {
        "t": token,
        "p": round(prob, 4),
        "m": round(margin, 3),
        "x": [[t, round(v, 3)] for t, v in ranked],
    }


async def stream_from_vllm(
    request_data: dict,
    session_id: str,
    vllm_endpoint: str,
    authorization: Optional[str] = None,
    with_logprobs: bool = False,
    top_logprobs: int = 5,
):
    """Async generator that yields (content_chunk, reasoning_chunk, token_count, reasoning_tokens, logprobs, prompt_tokens) per SSE event.

    ``logprobs`` is ``{"r": [...], "c": [...]}``: the per-token confidence entries
    consumed by this event's reasoning (``r``) and content (``c``) chunks. Each
    entry is ``{"t": token, "p": prob, "m": margin, "x": top_candidates}``.

    Thinking content is extracted from:
      - delta.reasoning  (vLLM native field for Qwen3 thinking)
      - Inline <think>…</think> blocks inside delta.content

    The caller can break out of the loop at any time; the httpx stream will be
    closed cleanly via the context-manager __aexit__ path.
    
    Args:
        request_data: The request payload to send to vLLM
        session_id: Session ID for logging
        vllm_endpoint: vLLM endpoint URL (e.g., "http://localhost:8000")
    
    Yields:
        Tuple of (content_chunk, reasoning_chunk, token_count, reasoning_tokens)
    """
    # Pass through all client parameters (transparent proxy).
    # Only strip de2generator internal markers (keys starting with "_").
    payload = {k: v for k, v in request_data.items() if not k.startswith("_")}
    payload["stream"] = True

    # Ask the backend to include token usage in the stream. Without this, neither
    # sglang nor vLLM send a `usage` chunk while streaming, so input/reasoning
    # token counts would be unavailable (prompt/tokens show as 0).
    _existing_so = payload.get("stream_options")
    payload["stream_options"] = {**_existing_so, "include_usage": True} if isinstance(_existing_so, dict) else {"include_usage": True}

    # Logprobs feature: the global UI toggle decides whether the LLM is asked for them.
    _lp_cap = max(1, int(top_logprobs or 5))
    if with_logprobs:
        payload["logprobs"] = True
    if payload.get("logprobs") is True:
        # Clamp to the server's configured maximum: requests above it are rejected
        # outright (and the client may not know the limit).
        try:
            _lp_want = int(payload.get("top_logprobs") or _lp_cap)
        except (TypeError, ValueError):
            _lp_want = _lp_cap
        payload["top_logprobs"] = max(1, min(_lp_want, _lp_cap))
    asked_logprobs = bool(with_logprobs)
    # Logprobs may also have been requested by the client itself (transparent proxy).
    logprobs_active = asked_logprobs or payload.get("logprobs") is True
    
    # Verify chat_template_kwargs is in payload (critical for thinking control)
    if "chat_template_kwargs" not in payload:
        log.warning(f"[{session_id}] WARNING: chat_template_kwargs NOT in payload sent to vLLM. This may cause unexpected thinking behavior.")
    else:
        ctk = payload.get("chat_template_kwargs", {})
        log.debug(f"[{session_id}] Sending to vLLM with chat_template_kwargs: {ctk}")

    in_think_tag = False
    token_count  = 0
    reasoning_tokens = 0  # Track tokens consumed by thinking from vLLM usage
    prompt_tokens = 0     # Input tokens reported by the backend usage field
    line_count   = 0
    cum_thinking = 0
    cum_response = 0
    lp_buf: list = []          # buffered per-token entries awaiting consumption
    saw_logprobs = False       # LLM did return logprobs

    headers = {"Content-Type": "application/json"}
    if authorization:
        headers["Authorization"] = authorization

    def _consume(n_chars: int) -> list:
        """Pop buffered tokens covering the next n_chars of generated text."""
        out: list = []
        taken = 0
        while lp_buf and taken < n_chars:
            out.append(lp_buf.pop(0))
            taken += len(out[-1].get("t") or "")
        return out

    try:
        # Deadlines are enforced per line below; the httpx read timeout only bounds the wait for headers.
        timeouts = httpx.Timeout(connect=10.0, read=max(VLLM_PREFILL_TIMEOUT, VLLM_STALL_TIMEOUT), write=30.0, pool=10.0)
        async with httpx.AsyncClient(timeout=timeouts) as client:
            resp = await client.send(
                client.build_request(
                    "POST",
                    f"{vllm_endpoint}/v1/chat/completions",
                    json=payload,
                    headers=headers,
                ),
                stream=True,
            )
            if resp.status_code >= 400 and logprobs_active:
                detail = (await resp.aread()).decode("utf-8", "replace")
                if "logprob" in detail.lower():
                    # LLM cannot serve logprobs -> flag it and retry transparently.
                    log.warning(
                        f"[{session_id}] LLM rejected logprobs ({resp.status_code}): "
                        f"{detail[:300]} — disabling for this request"
                    )
                    if asked_logprobs:
                        ui_logprobs_unsupported()
                    asked_logprobs = False
                    with_logprobs = False
                    logprobs_active = False
                    payload.pop("logprobs", None)
                    payload.pop("top_logprobs", None)
                    resp = await client.send(
                        client.build_request(
                            "POST",
                            f"{vllm_endpoint}/v1/chat/completions",
                            json=payload,
                            headers=headers,
                        ),
                        stream=True,
                    )

            try:
                resp.raise_for_status()

                lines = resp.aiter_lines().__aiter__()
                first_data_seen = False
                finish_seen = False
                while True:
                    # After finish_reason we still read a short while to capture the
                    # trailing usage chunk and [DONE]; beyond that treat as stream end.
                    try:
                        raw_line = await asyncio.wait_for(
                            lines.__anext__(),
                            _POST_FINISH_GRACE if finish_seen
                            else (VLLM_STALL_TIMEOUT if first_data_seen else VLLM_PREFILL_TIMEOUT),
                        )
                    except StopAsyncIteration:
                        break
                    except asyncio.TimeoutError:
                        if finish_seen:
                            break
                        phase = "stalled mid-stream" if first_data_seen else "no first token (prefill)"
                        limit = VLLM_STALL_TIMEOUT if first_data_seen else VLLM_PREFILL_TIMEOUT
                        raise BackendTimeoutError(f"Backend {phase} for {limit:.0f}s — aborted") from None
                    line_count += 1
                    if not raw_line.startswith("data: "):
                        continue
                    first_data_seen = True
                    body = raw_line[6:]
                    if body.strip() == "[DONE]":
                        break

                    try:
                        chunk = json.loads(body)

                    except json.JSONDecodeError:
                        log.warning(f"[{session_id}] JSON decode error on line {line_count}: {body[:200]}")
                        continue

                    # A streaming backend reports errors as HTTP 200 + an error event,
                    # so they must be inspected here (they carry no "choices").
                    if isinstance(chunk.get("error"), dict):
                        err_msg = str(chunk["error"].get("message") or chunk["error"])[:300]
                        if "logprob" in err_msg.lower():
                            if asked_logprobs:
                                ui_logprobs_unsupported()
                            # Drop logprobs from the caller's request too, so the next
                            # supervisor attempt cannot fail the same way.
                            for _k in ("logprobs", "top_logprobs"):
                                payload.pop(_k, None)
                                request_data.pop(_k, None)
                            log.warning(
                                f"[{session_id}] LLM rejected logprobs: {err_msg} — "
                                f"stripped, retrying without them"
                            )
                            # End this attempt with no output: that is the signal the
                            # supervisor uses to retry, and the keys are gone by now.
                            break
                        raise RuntimeError(f"vLLM stream error: {err_msg}")

                    # Token usage arrives, when enabled, in a final chunk with NO
                    # choices — parse it before the empty-choices skip below.
                    usage = chunk.get("usage")
                    if usage:
                        token_count      = usage.get("completion_tokens", token_count)
                        reasoning_tokens = usage.get("reasoning_tokens", reasoning_tokens)
                        prompt_tokens    = usage.get("prompt_tokens", prompt_tokens)

                    choices = chunk.get("choices", [])
                    if not choices and not usage:
                        continue

                    delta         = (choices[0].get("delta") or {}) if choices else {}
                    finish_reason = choices[0].get("finish_reason") if choices else None

                    # Collect per-token confidence data if the backend provided it.
                    if with_logprobs and choices:
                        for raw_entry in ((choices[0].get("logprobs") or {}).get("content") or []):
                            compact = _entry_to_compact(raw_entry)
                            if compact:
                                lp_buf.append(compact)
                                saw_logprobs = True

                    # Backends expose native reasoning as either field name.
                    reasoning_chunk: str = (
                        delta.get("reasoning")
                        or delta.get("reasoning_content")
                        or ""
                    )
                    content_chunk:   str = delta.get("content") or ""

                    # Handle inline <think>…</think> in content
                    if not in_think_tag and "<think>" in content_chunk:
                        prefix, content_chunk = content_chunk.split("<think>", 1)
                        in_think_tag = True
                    else:
                        prefix = ""
                    if in_think_tag:
                        if "</think>" in content_chunk:
                            in_think_tag = False
                            parts = content_chunk.split("</think>", 1)
                            reasoning_chunk += parts[0]
                            content_chunk    = prefix + parts[1]
                        else:
                            reasoning_chunk += content_chunk
                            content_chunk    = prefix

                    # Fallback token estimate when the backend sent no usage for it
                    if not usage and content_chunk:
                        token_count += len(content_chunk.split())

                    # Map the buffered tokens onto this event's reasoning/content text.
                    lp_items = {
                        "r": _consume(len(reasoning_chunk)) if reasoning_chunk else [],
                        "c": _consume(len(content_chunk)) if content_chunk else [],
                    }

                    cum_thinking += len(reasoning_chunk)
                    cum_response += len(content_chunk)

                    yield content_chunk, reasoning_chunk, token_count, reasoning_tokens, lp_items, prompt_tokens

                    if finish_reason:
                        if usage:
                            break  # usage already arrived with the finish chunk
                        # Keep reading briefly: the usage chunk and [DONE] follow
                        # the finish chunk with include_usage enabled.
                        finish_seen = True
            finally:
                await resp.aclose()

    finally:
        if asked_logprobs:
            if saw_logprobs:
                ui_logprobs_supported()
            else:
                # Toggle is on but the LLM silently ignored the request.
                ui_logprobs_unsupported()
        log.info(f"[{session_id}] Stream closed ({line_count} lines) — thinking={cum_thinking} chars  response={cum_response} chars  tokens≈{token_count}  reasoning_tokens={reasoning_tokens}  prompt_tokens={prompt_tokens}  logprobs={'yes' if saw_logprobs else 'no'}")
