"""OpenAI-compatible response assembly.

The supervisor accumulates text; this module turns the session state into the
single ``chat.completion`` object the client receives, including the
``de2generator`` stats block and the thinking-state markers that tell a client
whether the answer survived a thinking-budget stall.
"""

import time
from typing import Optional, Tuple

from core.models import GenerationSession
from translation import merged_translation
from utils import get_logger

log = get_logger()


def estimate_token_split(total_output_tokens: int, reasoning_chars: int,
                         answer_chars: int) -> Tuple[int, int]:
    """Split total output tokens into (est_reasoning, est_answer) by char ratio.

    The estimate is rough — it assumes tokens-per-char is similar for reasoning
    and answer text — but the two parts always add up to the exact total reported
    by the backend, so downstream accounting stays consistent.
    """
    if total_output_tokens <= 0:
        return 0, 0
    total_chars = reasoning_chars + answer_chars
    if total_chars <= 0:
        return 0, total_output_tokens
    est_reasoning = int(round(total_output_tokens * reasoning_chars / total_chars))
    est_reasoning = max(0, min(est_reasoning, total_output_tokens))
    return est_reasoning, total_output_tokens - est_reasoning


def clip_logprobs(lps: list, max_chars: int) -> list:
    """Truncate a per-token confidence list so it covers at most `max_chars` of text.

    Keeps the token data in sync with checkpoint snippets that were themselves
    truncated before being broadcast.
    """
    out: list = []
    total = 0
    for entry in lps:
        total += len(entry.get("t") or "")
        if total > max_chars:
            break
        out.append(entry)
    return out


def _thinking_markers(session: GenerationSession, content: str,
                     thinking_exhausted: bool) -> Tuple[Optional[str], str]:
    """Annotate the reasoning text and classify truncation for the stats block."""
    thinking_content = session.thinking_text
    truncation_status = "no_truncation"
    if thinking_exhausted and thinking_content:
        if len(content.strip()) > 0:
            thinking_content = thinking_content + "\n[AUTOMATIC-RETRY-SUCCESSFUL-AFTER-THINKING-STALL]"
            truncation_status = "thinking_budget_exhausted_but_recovered"
        else:
            thinking_content = thinking_content + "\n[TRUNCATED-BECAUSE-OVERTHINKING-BUDGET]"
            truncation_status = "thinking_budget_exhausted"

    log.warning(
        f"[{session.request_id}] build_response: thinking_exhausted={thinking_exhausted}, "
        f"thinking_content length={len(thinking_content or '')}, "
        f"truncated={bool(thinking_exhausted and session.thinking_text)}"
    )
    return thinking_content, truncation_status


def _final_content(session: GenerationSession, content: str) -> Tuple[str, bool]:
    """Translated answer when every content checkpoint was translated, else original."""
    translated = merged_translation(session.checkpoints, session.translated_checkpoints) \
        if (session.translated_checkpoints and session.translation_language) else None
    if translated is not None:
        log.info(
            f"[{session.request_id}] Using translated content ({len(translated)} chars, "
            f"lang={session.translation_language}) with overlap merging"
        )
        return translated, True
    if session.translated_checkpoints and session.translation_language:
        log.warning(
            f"[{session.request_id}] Translation incomplete — some checkpoints missing, "
            f"returning original content"
        )
    return content, False


def build_response(session: GenerationSession, content: str,
                   thinking_exhausted: bool = False) -> dict:
    """Build the OpenAI-compatible completion object for a finished session."""
    thinking_content, truncation_status = _thinking_markers(session, content, thinking_exhausted)
    final_answer, translation_applied = _final_content(session, content)

    elapsed_total = time.time() - session.start_time
    total_completion_tokens = session.token_count
    thinking_tokens_final = session.thinking_tokens if session.thinking_tokens > 0 else 0
    output_only_tokens = max(0, total_completion_tokens - thinking_tokens_final)
    tokens_per_second = total_completion_tokens / max(elapsed_total, 0.1)

    session.truncation_status = truncation_status
    session.output_tokens = output_only_tokens
    session.reasoning_tokens = thinking_tokens_final

    # Estimated reasoning/answer split of the (exact) total output tokens, based
    # on the ratio between reasoning and answer generated text. The two parts
    # always sum to total_completion_tokens.
    est_reasoning_tokens, est_answer_tokens = estimate_token_split(
        total_completion_tokens,
        len((session.thinking_text or "").strip()),
        len((content or "").strip()),
    )

    message = {"role": "assistant", "content": final_answer}
    # Only include reasoning if non-empty after stripping whitespace
    thinking_content = thinking_content.strip() if thinking_content else None
    if thinking_content:
        message["reasoning_content"] = thinking_content

    if thinking_content:
        log.warning(
            f"[{session.request_id}] Response includes reasoning "
            f"({len(thinking_content)} chars): {thinking_content[:100]}..."
        )

    return {
        "id":      f"chatcmpl-{session.request_id}",
        "object":  "chat.completion",
        "created": int(time.time()),
        "model":   session.model,
        "choices": [
            {
                "index":         0,
                "message":       message,
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens":     session.input_tokens,
            "completion_tokens": session.token_count,
            "total_tokens":      session.input_tokens + session.token_count,
            "reasoning_tokens":  est_reasoning_tokens,
        },
        "de2generator": {
            "request_id":                    session.request_id,
            "recovery_attempts":             session.recovery_attempts,
            "checkpoints":                   len(session.checkpoints),
            "elapsed_total_seconds":         elapsed_total,
            "tokens_per_second":             round(tokens_per_second, 2),
            "thinking_tokens":               thinking_tokens_final,
            "output_tokens":                 output_only_tokens,
            "total_tokens":                  total_completion_tokens,
            "ttft_seconds":                  round(session.ttft_seconds, 1) if session.ttft_seconds is not None else None,
            "thinking_phase_seconds":        round(session.thinking_phase_duration, 1),
            "rejected_checkpoints_count":    session.rejected_checkpoints_count,
            "rejected_checkpoints_tokens":   session.rejected_checkpoints_tokens,
            "truncation_status":             truncation_status,
            "translation_language":          session.translation_language or None,
            "translation_applied":           translation_applied,
            "input_tokens":                  session.input_tokens,
            "reasoning_tokens_est":          est_reasoning_tokens,
            "answer_tokens_est":             est_answer_tokens,
            "total_output_tokens":           total_completion_tokens,
        },
    }
