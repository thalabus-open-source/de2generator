"""Semantic LLM-based degeneration detection."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import httpx

from core.constants import (
    SEMANTIC_VERIFIER_ENDPOINT,
    SEMANTIC_VERIFIER_MODEL,
    SEMANTIC_VERIFIER_TIMEOUT,
    SEMANTIC_MIN_BLOCK_CHARS,
    SEMANTIC_MAX_MODEL_LEN,
    SEMANTIC_MAX_ANSWER_TOKENS,
    SEMANTIC_SAFETY_BUFFER_TOKENS,
    THINKING_CHARS_PER_TOKEN,
    SEMANTIC_MIN_FAILING_CATEGORIES,
    SEMANTIC_LLM_COOLDOWN_DURATION,
)
from utils import get_logger

log = get_logger()


_SEMANTIC_PROMPT_TEMPLATE = """\
You are a generation quality detector. Evaluate the NEW BLOCK against each category below.

PREVIOUS CONTEXT (last part of what was already generated):
{context}

NEW BLOCK (text to evaluate):
{new_block}

For each category, respond DETECTED if the described problem IS present, NO_PROBLEM if it is NOT present, or UNCLEAR if you cannot determine.
Reply with EXACTLY one line per category in the format CATEGORY:VERDICT:

CHARACTER_REPETITION: Are characters or short sequences repeated abnormally (e.g. "aaaa", "....", "!!!!" , "xxxxx")?
WORD_REPETITION: Is the same word repeated excessively (3+ times) in close proximity?
PHRASE_REPETITION: Are phrases or sentences repeated verbatim from the context or within the block itself?
SEMANTIC_STAGNATION: Does the block fail to introduce any new information, merely rephrasing or echoing the context?
POOR_STRUCTURE: Are sentences malformed, punctuation incorrect, or does the text lack logical flow?
INCOHERENCE: Is the text unintelligible, gibberish, or does it contain corrupted character sequences?

Output (exactly 6 lines, no extra text):"""


def _fit_to_context(context: str, new_block: str) -> tuple[str, str]:
    """Keep only the latest characters so prompt + answer + buffer fit SEMANTIC_MAX_MODEL_LEN.

    The new block has priority; the context gets whatever room is left. Both are cut from the front.
    """
    if SEMANTIC_MAX_MODEL_LEN <= 0:
        return context, new_block
    prompt_tokens = SEMANTIC_MAX_MODEL_LEN - SEMANTIC_MAX_ANSWER_TOKENS - SEMANTIC_SAFETY_BUFFER_TOKENS
    template_chars = len(_SEMANTIC_PROMPT_TEMPLATE) - len("{context}") - len("{new_block}")
    budget = max(0, prompt_tokens * THINKING_CHARS_PER_TOKEN - template_chars)
    if len(new_block) >= budget:
        return "", new_block[len(new_block) - budget:] if budget else ""
    room = budget - len(new_block)
    return (context[-room:] if room else ""), new_block


class SemanticLLMAvailabilityTracker:
    """Tracks semantic LLM model availability with automatic cooldown on failure.
    
    When the semantic verifier becomes unavailable (connection error, timeout, etc.),
    it disables semantic validation for the configured cooldown period to avoid
    repeated failed attempts. After the cooldown expires, validation is automatically retried.
    """
    
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cooldown_until: float = 0.0  # Timestamp when cooldown expires (0 = no cooldown)
        self._failure_count: int = 0  # Number of consecutive failures
        self._last_error: str = ""  # Last error message for logging
    
    def is_available(self) -> bool:
        """Check if semantic LLM validation should be attempted."""
        with self._lock:
            if self._cooldown_until == 0.0:
                return True  # No cooldown active
            if time.time() >= self._cooldown_until:
                # Cooldown expired, reset and allow retry
                self._cooldown_until = 0.0
                self._failure_count = 0
                log.info("Semantic LLM cooldown expired — validation re-enabled")
                return True
            # Still in cooldown
            remaining = self._cooldown_until - time.time()
            if int(remaining) % 60 == 0 or remaining < 1:  # Log every minute or on last second
                log.warning(f"Semantic LLM in cooldown for {remaining:.1f}s more...")
            return False
    
    def record_failure(self, error_msg: str) -> None:
        """Record a failure and start/extend the cooldown period."""
        with self._lock:
            self._failure_count += 1
            self._last_error = error_msg
            self._cooldown_until = time.time() + SEMANTIC_LLM_COOLDOWN_DURATION
            log.error(
                f"Semantic LLM failure #{self._failure_count}: {error_msg} — "
                f"entering cooldown (until {time.ctime(self._cooldown_until)})"
            )
    
    def get_status(self) -> dict:
        """Return current availability status for monitoring/debugging."""
        with self._lock:
            if self._cooldown_until == 0.0:
                return {"available": True, "cooldown_active": False}
            remaining = max(0.0, self._cooldown_until - time.time())
            if remaining <= 0:
                return {"available": True, "cooldown_active": False}
            return {
                "available": False,
                "cooldown_active": True,
                "cooldown_seconds_remaining": remaining,
                "failure_count": self._failure_count,
                "last_error": self._last_error,
            }


# Global semantic LLM availability tracker
_semantic_llm_tracker = SemanticLLMAvailabilityTracker()


class SemanticProgressDetector:
    """Detects semantic degeneration via an external verifier LLM.
    
    Validation runs in background threads so the main generation stream is never blocked.
    Results may arrive out of order; the three-strike rule checks checkpoint POSITION ORDER
    (not arrival order): if any 3 consecutive positions [N-2, N-1, N] all return "no",
    rollback is triggered to the checkpoint before position N-2.
    """
    
    def __init__(self, request_id: str) -> None:
        self._request_id = request_id
        self._lock = threading.Lock()
        # local_cp_idx -> "yes" | "no" | "unclear" | None (None = pending)
        self._results: dict = {}
        # local_cp_idx -> global session checkpoint index (for UI broadcasts)
        self._global_idx: dict = {}
        self._rollback_triggered = False
        self._rollback_local_idx: Optional[int] = None
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="semantic-v")
        self._shutdown = False
    
    def submit(self, local_cp_idx: int, new_block: str, context: str, global_cp_idx: int, ui_update_callback) -> None:
        """Queue a checkpoint for async semantic validation. Returns immediately.
        
        Args:
            local_cp_idx: Local checkpoint index
            new_block: New text block to validate
            context: Previous context
            global_cp_idx: Global session checkpoint index
            ui_update_callback: Callback for UI updates (ui_update_checkpoint_semantic)
        """
        with self._lock:
            if self._shutdown:
                return
            self._global_idx[local_cp_idx] = global_cp_idx
            if len(new_block.strip()) < SEMANTIC_MIN_BLOCK_CHARS:
                # Block too short — skip, treat as unclear
                log.info(f"[{self._request_id}] Semantic cp[{local_cp_idx}] skipped (block {len(new_block.strip())} chars < {SEMANTIC_MIN_BLOCK_CHARS} min)")
                self._results[local_cp_idx] = "unclear"
                # Notify UI with tooltip explaining why
                tooltip = f"Block too short for semantic analysis ({len(new_block.strip())} chars, minimum {SEMANTIC_MIN_BLOCK_CHARS})"
                ui_update_callback(self._request_id, global_cp_idx, "unclear", "block too short", tooltip)
                return
            
            # Check if semantic LLM is available (not in cooldown)
            if not _semantic_llm_tracker.is_available():
                # Semantic LLM is in cooldown — skip validation
                status = _semantic_llm_tracker.get_status()
                remaining = status.get("cooldown_seconds_remaining", 0)
                log.info(f"[{self._request_id}] Semantic cp[{local_cp_idx}] skipped (LLM unavailable, cooldown for {remaining:.0f}s more)")
                self._results[local_cp_idx] = "llm_unavailable"
                tooltip = f"Semantic LLM unavailable (cooldown: {remaining:.0f}s remaining). Validation skipped."
                ui_update_callback(self._request_id, global_cp_idx, "llm_unavailable", "llm unavailable", tooltip)
                return
            
            self._results[local_cp_idx] = None  # pending
            log.info(f"[{self._request_id}] Semantic cp[{local_cp_idx}] queued ({len(new_block.strip())} chars)")
            # Show pending state in UI immediately
            ui_update_callback(self._request_id, global_cp_idx, "pending", "", "Semantic validation in progress...")
            self._executor.submit(self._validate, local_cp_idx, new_block, context, ui_update_callback)
    
    def _validate(self, local_cp_idx: int, new_block: str, context: str, ui_update_callback) -> None:
        """Runs in background thread: calls verifier LLM and stores result."""
        verdict = "unclear"
        reason = ""
        full_response = ""
        try:
            # Latest characters only, so the prompt fits the verifier's context window
            context, new_block = _fit_to_context(context, new_block)
            prompt = _SEMANTIC_PROMPT_TEMPLATE.format(context=context, new_block=new_block)
            payload = {
                "model": SEMANTIC_VERIFIER_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": SEMANTIC_MAX_ANSWER_TOKENS,
                "temperature": 0.0,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            with httpx.Client(timeout=SEMANTIC_VERIFIER_TIMEOUT) as client:
                resp = client.post(SEMANTIC_VERIFIER_ENDPOINT, json=payload)
            resp.raise_for_status()
            choice = resp.json()["choices"][0]["message"]
            # Qwen3 thinking models may return answer in "reasoning" when content is null
            raw = (choice.get("content") or choice.get("reasoning") or "").strip()
            full_response = raw  # Store for tooltip/UI display
            log.info(
                f"[{self._request_id}] Semantic LLM input cp[{local_cp_idx}]:\n"
                f"  context ({len(context)} chars): {context[:200]!r}\n"
                f"  new_block ({len(new_block)} chars): {new_block[:200]!r}"
            )
            log.info(f"[{self._request_id}] Semantic LLM response cp[{local_cp_idx}]:\n{raw}")
            
            # Parse multi-line CATEGORY:VERDICT format.
            # Vocabulary: DETECTED = problem present, NO_PROBLEM = healthy.
            # A checkpoint counts as a strike only if SEMANTIC_MIN_FAILING_CATEGORIES
            # or more categories are DETECTED (avoids false positives from single-category hits).
            category_verdicts: dict = {}
            for line in raw.splitlines():
                line = line.strip()
                if ":" in line:
                    cat, _, vpart = line.partition(":")
                    vpart = vpart.strip().upper()
                    v = "detected" if "DETECTED" in vpart else "no_problem" if "NO_PROBLEM" in vpart else "unclear"
                    category_verdicts[cat.strip().upper()] = v
            
            if category_verdicts:
                failing = [cat for cat, v in category_verdicts.items() if v == "detected"]
                if len(failing) >= SEMANTIC_MIN_FAILING_CATEGORIES:
                    verdict = "no"
                    reason = ", ".join(failing)
                elif all(v == "no_problem" for v in category_verdicts.values()):
                    verdict = "yes"
                else:
                    # Some DETECTED but below threshold, or UNCLEAR entries — treat as unclear
                    verdict = "unclear"
                    if failing:
                        reason = f"below threshold ({len(failing)}/{SEMANTIC_MIN_FAILING_CATEGORIES}): " + ", ".join(failing)
            else:
                # Fallback: no structured lines found
                raw_upper = raw.upper()
                verdict = "no" if "DETECTED" in raw_upper else "yes" if "NO_PROBLEM" in raw_upper else "unclear"
        
        except Exception as exc:
            error_msg = str(exc)
            log.warning(f"[{self._request_id}] Semantic verifier error cp[{local_cp_idx}]: {error_msg}")
            # Record failure and trigger cooldown
            _semantic_llm_tracker.record_failure(error_msg)
            verdict = "llm_unavailable"
            reason = "llm unavailable"
            full_response = f"Semantic LLM error: {error_msg}"
        
        with self._lock:
            if self._shutdown:
                return
            self._results[local_cp_idx] = verdict
            global_idx = self._global_idx.get(local_cp_idx)
        log.info(f"[{self._request_id}] Semantic cp[{local_cp_idx}] → {verdict}" + (f" ({reason})" if reason else ""))
        if global_idx is not None:
            ui_update_callback(self._request_id, global_idx, verdict, reason, full_response)
        self._check_strikes(local_cp_idx)
    
    def _check_strikes(self, arrived_idx: int) -> None:
        """Check if 3 consecutive local checkpoints (by position) all returned 'no' (= DETECTED >= threshold)."""
        with self._lock:
            if self._rollback_triggered:
                return
            # Examine every window of 3 that contains arrived_idx
            for start in range(max(0, arrived_idx - 2), arrived_idx + 1):
                end = start + 2
                if all(self._results.get(i) == "no" for i in range(start, end + 1)):
                    self._rollback_triggered = True
                    self._rollback_local_idx = max(0, start - 1)
                    log.warning(
                        f"[{self._request_id}] Semantic 3-strike: cps[{start}-{end}] all DETECTED>={SEMANTIC_MIN_FAILING_CATEGORIES} "
                        f"→ rollback to local cp[{self._rollback_local_idx}]"
                    )
                    return
    
    def get_result(self, local_cp_idx: int) -> Optional[str]:
        """Get validation result for a checkpoint."""
        with self._lock:
            return self._results.get(local_cp_idx)
    
    @property
    def rollback_triggered(self) -> bool:
        """Check if rollback has been triggered."""
        with self._lock:
            return self._rollback_triggered
    
    @property
    def rollback_local_idx(self) -> Optional[int]:
        """Get the checkpoint index to rollback to."""
        with self._lock:
            return self._rollback_local_idx
    
    def shutdown(self) -> None:
        """Shutdown the executor."""
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        self._executor.shutdown(wait=False, cancel_futures=True)
