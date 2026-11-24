"""The supervision state machine.

``GenerationSupervisor`` owns one client request from parsing to response.  It
drives three nested loops, each with a single responsibility:

``run()``             outer *full restarts* — discard everything, start over
  ``_run_attempt()``  inner *recovery attempts* — stream once, then decide
    ``_ingest()``     per chunk — checkpoint healthy text, abort on degeneration

What happens after a stream ends is a short ordered list of recovery checks
(``_decide_*``): each either retries with a specific strategy or falls through.
Keeping them as separate methods means every strategy can be read, tested and
tuned on its own — the ordering in ``_post_stream`` is the contract.

Failure modes handled:

1. Thinking-budget exhaustion — thinking only, no answer → retry with thinking
   disabled and the interrupted reasoning injected as context.
2. Repetition/entropy degeneration — abort mid-stream, roll back to the last
   healthy checkpoint, continue from there.
3. Thinking loop — the same reasoning block verbatim, repeatedly → keep the
   reasoning up to the loop point and answer without thinking.
4. Semantic degeneration — the async verifier's three-strike rule pins the
   rollback checkpoint.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple

from core.config import get_config_value
from core.constants import (
    ENTROPY_MAX_HEALTHY,
    ENTROPY_MIN_HEALTHY,
    MAX_FULL_RESTARTS,
    MAX_RECOVERY_ATTEMPTS,
    THINKING_BUDGET_EXHAUSTION_THRESHOLD,
    THINKING_CHARS_PER_TOKEN,
    THINKING_LOOP_MAX_REPEATS,
    THINKING_LOOP_MIN_CHARS,
)
from core.models import Checkpoint, DetectionResult, GenerationSession
from detection import SemanticProgressDetector, detect_content_type
from recovery import (
    CheckpointManager,
    build_continuation_messages,
    build_nothink_request,
    is_thinking_exhausted,
)
from response import build_response, clip_logprobs, estimate_token_split
from streaming import stream_from_vllm
from translation import TranslationService, apply_translations
from ui import (
    _ui_broadcast,
    _ui_find,
    ui_add_checkpoint,
    ui_logprobs_requested,
    ui_start_interaction,
    ui_update_checkpoint_semantic,
    ui_update_thinking_tokens,
    ui_update_token_counts,
    ui_update_ttft,
)
from utils import get_logger, print_block, print_thinking_block

from .request_context import RequestContext, parse_request
from .telemetry import log_telemetry

log = get_logger()

DEFAULT_MAX_TOKENS = 32768
MAX_TOKENS_CEILING = 36768
THINKING_SNIPPET_CHARS = 2000
REJECT_EXCERPT_CHARS = 1000
REJECT_EXCERPT_TAIL = 500


class _Action(Enum):
    FINISH = "finish"
    RETRY_ATTEMPT = "retry_attempt"


@dataclass
class _Verdict:
    """What the supervisor does next."""

    action: _Action
    content: str = ""

    @property
    def is_final(self) -> bool:
        return self.action is _Action.FINISH


@dataclass
class _Event:
    """One streamed backend event."""

    content: str = ""
    reasoning: str = ""
    tokens: int = 0
    reasoning_tokens: int = 0
    lp: dict = field(default_factory=dict)
    prompt_tokens: int = 0


@dataclass
class _Attempt:
    """Mutable state of a single streaming attempt."""

    checkpoint_start:    int = 0            # session checkpoints when the attempt began
    start_tokens:        int = 0            # session token count when the attempt began
    prompt_tokens:       int = 0            # input tokens reported by this attempt
    token_count:         int = 0            # output tokens reported by this attempt
    thinking_text:       str = ""
    response_text:       str = ""
    candidate_text:      str = ""           # accumulated text + this attempt's response
    thinking_char_count: int = 0            # thinking length while content is still empty
    first_token_seen:    bool = False
    aborted:             Optional[DetectionResult] = None
    detection:           Optional[DetectionResult] = None
    prev_cp_len:         int = 0            # char offset for slice-printing
    prev_thinking_len:   int = 0            # thinking offset for thinking checkpoints
    thinking_start_wall: Optional[float] = None
    thinking_elapsed:    float = 0.0
    pending_lp_r:        list = field(default_factory=list)  # thinking tokens awaiting a checkpoint
    pending_lp_c:        list = field(default_factory=list)  # content tokens awaiting a checkpoint
    lp_c_all:            list = field(default_factory=list)  # every accepted content token
    nothink:             bool = False       # thinking already disabled for this attempt
    thinking_loop:       Optional[Tuple[str, int]] = None    # (reason, thinking chars to keep)
    thinking_cp_ends:    list = field(default_factory=list)  # len(thinking_text) at each checkpoint
    chunk_seen:          dict = field(default_factory=dict)  # normalized block -> (first end, count)


def _entropy_display(entropy: Optional[float]) -> str:
    return "N/A" if entropy is None else f"{entropy:.2f}"


def _entropy_suffix(entropy: Optional[float]) -> str:
    return "" if entropy is None else f" | entropy: {entropy:.2f}"


def _chunk_label(attempt: int, max_attempts: int) -> str:
    """Suffix shared by every rejection message, e.g. ``attempt 2/4``."""
    return f"attempt {attempt + 1}/{max_attempts}"


class GenerationSupervisor:
    """Supervises one generation from parsed request to final response."""

    def __init__(
        self,
        context: RequestContext,
        request_id: Optional[str] = None,
        *,
        request: Optional[object] = None,
        authorization: Optional[str] = None,
    ) -> None:
        self.context = context
        self.request = request
        self.authorization = authorization

        self.session = GenerationSession(
            request_id=request_id or str(uuid.uuid4()),
            model=context.model,
            messages=list(context.messages),
            original_request=dict(context.request_data),
            thinking_was_requested=bool(
                context.thinking_budget_tokens is not None and context.thinking_budget_tokens > 0
            ),
            translation_language=context.translate_to or "",
        )

        log.info(
            f"[{self.session.request_id}] Starting generation — model={self.session.model}  "
            f"enable_thinking={context.enable_thinking}  "
            f"thinking_budget={context.thinking_budget_tokens}  "
            f"reasoning_effort={context.reasoning_effort}"
        )
        ui_start_interaction(
            self.session.request_id, self.session.model,
            context.thinking_budget_tokens, context.enable_thinking,
            context.model_parameters, context.agent_name, context.goal_id,
            self.session.messages,
        )

        self.semantic = SemanticProgressDetector(self.session.request_id)
        self.translation_svc: Optional[TranslationService] = (
            TranslationService(self.session.request_id, context.translate_to)
            if context.translate_to else None
        )

        # State that is reset on every full restart
        self.cp_manager = CheckpointManager()
        self.accumulated_text: str = ""
        self.current_request: dict = dict(context.request_data)
        self.initial_thinking_text: str = ""
        self.thinking_exhausted: bool = False
        self.last_checkpoint_token: int = 0
        self.attempt_index: int = 0
        self._attempt_label = ""

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def run(self) -> Tuple[dict, GenerationSession]:
        """Supervise the generation and return ``(response, session)``."""
        owner_task = asyncio.current_task()
        if owner_task is not None:
            owner_task.add_done_callback(self._shutdown_background_services)

        max_attempts = MAX_RECOVERY_ATTEMPTS
        max_restarts = MAX_FULL_RESTARTS

        for full_restart in range(max_restarts + 1):
            self._begin_restart(full_restart)

            for attempt in range(max_attempts + 1):
                self.attempt_index = attempt
                self._attempt_label = _chunk_label(attempt, max_attempts)
                verdict = await self._run_attempt(attempt)
                if verdict.is_final:
                    return await self._finalize(verdict.content)

            # All recovery attempts failed; a full restart is still a distinct option
            if full_restart < max_restarts:
                continue

            log.error(
                f"[{self.session.request_id}] Max full restarts ({max_restarts}) "
                f"reached after {max_attempts + 1} recovery attempts each. "
                f"Returning best available output."
            )
            print_block("max full restarts reached — returning best output",
                        False, self.accumulated_text)
            ui_add_checkpoint(
                self.session.request_id, "error",
                "max full restarts reached — returning best output",
                self.accumulated_text[-REJECT_EXCERPT_TAIL:],
            )
            return await self._finalize(self.accumulated_text)

        log.error(
            f"[{self.session.request_id}] Unexpected: outer loop completed without "
            f"returning. This should never happen."
        )
        if self.translation_svc is not None:
            self.translation_svc.shutdown()
        return await self._finalize(self.accumulated_text)

    def _shutdown_background_services(self, _task: "asyncio.Task") -> None:
        """Stop the verifier/translator thread pools when the owning task goes away."""
        self.semantic.shutdown()
        if self.translation_svc is not None:
            self.translation_svc.shutdown()

    async def _finalize(self, content: str) -> Tuple[dict, GenerationSession]:
        """Close background services, flush translations and build the response."""
        self.semantic.shutdown()
        await apply_translations(self.session, self.translation_svc)
        return build_response(self.session, content, self.thinking_exhausted), self.session

    def _begin_restart(self, full_restart: int) -> None:
        """Reset all generation state and rebuild the per-restart services."""
        if full_restart > 0:
            log.warning(
                f"[{self.session.request_id}] Full restart {full_restart}/{MAX_FULL_RESTARTS} "
                f"— clearing all checkpoints and starting from original request"
            )
            print_block(
                f"full restart {full_restart}/{MAX_FULL_RESTARTS} "
                f"— clearing state and restarting from original request", False, "",
            )
            ui_add_checkpoint(
                self.session.request_id, "error",
                f"max recovery attempts reached — full restart "
                f"{full_restart}/{MAX_FULL_RESTARTS} from original request", "",
            )
            self.session.checkpoints.clear()
            self.session.token_count = 0
            self.session.recovery_attempts = 0
            self.session.rejected_checkpoints_count = 0
            self.session.rejected_checkpoints_tokens = 0
            self._restart_semantic_detector()
            self._restart_translation_service()

        self.cp_manager = CheckpointManager()
        self.last_checkpoint_token = 0
        self.accumulated_text = ""
        self.current_request = self._original_request()
        self.initial_thinking_text = ""
        self.thinking_exhausted = False

    def _original_request(self) -> dict:
        """A fresh copy of the (sanitised) client request.

        ``chat_template_kwargs`` is copied deeply enough that one attempt can never
        modify the client's request or a later attempt's payload by accident.
        """
        request = dict(self.context.request_data)
        if isinstance(request.get("chat_template_kwargs"), dict):
            request["chat_template_kwargs"] = dict(request["chat_template_kwargs"])
        return request

    def _continuation_request(self, checkpoint_text: str) -> dict:
        """The original request, asking the model to continue from ``checkpoint_text``."""
        request = self._original_request()
        request["messages"] = build_continuation_messages(self.session.messages, checkpoint_text)
        return request

    def _restart_semantic_detector(self) -> None:
        """A fresh three-strike count: the old one belongs to discarded output."""
        self.semantic.shutdown()
        self.semantic = SemanticProgressDetector(self.session.request_id)

    def _restart_translation_service(self) -> None:
        """Pending translations of a rejected attempt are worthless — start over."""
        if self.translation_svc is not None:
            self.translation_svc.shutdown()
            self.translation_svc = (
                TranslationService(self.session.request_id, self.context.translate_to)
                if self.context.translate_to else None
            )

    # ── one streaming attempt ────────────────────────────────────────────────

    async def _run_attempt(self, attempt: int) -> _Verdict:
        """Stream once, ingest every chunk, then decide what happens next."""
        state = self._begin_attempt()
        async for event in self._stream():
            if await self._client_gone():
                break
            if await self._ingest(event, state):
                break
        return await self._post_stream(attempt, state)

    def _begin_attempt(self) -> _Attempt:
        chat_template_kwargs = self.current_request.get("chat_template_kwargs") or {}
        if self.attempt_index == 0:
            log.debug(
                f"[{self.session.request_id}] Initial attempt: "
                f"chat_template_kwargs present="
                f"{('chat_template_kwargs' in self.current_request)}, "
                f"enable_thinking={chat_template_kwargs.get('enable_thinking', True)}, "
                f"content={chat_template_kwargs}"
            )
        return _Attempt(
            checkpoint_start=len(self.session.checkpoints),
            start_tokens=self.session.token_count,
            first_token_seen=self.session.ttft_seconds is not None,
            prev_cp_len=len(self.accumulated_text),
            nothink=chat_template_kwargs.get("enable_thinking") is False,
        )

    async def _client_gone(self) -> bool:
        """True once the client has disconnected; the stream is then abandoned."""
        if self.request is None or not await self.request.is_disconnected():
            return False
        log.warning(f"[{self.session.request_id}] Client disconnected; aborting stream")
        return True

    async def _stream(self) -> AsyncIterator[_Event]:
        """Yield backend events for the request currently held in ``current_request``."""
        async for (
            content_chunk, reasoning_chunk, tokens, reasoning_tokens, lp_items, prompt_tokens
        ) in stream_from_vllm(
            self.current_request,
            self.session.request_id,
            vllm_endpoint=self.context.endpoint,
            authorization=self.authorization,
            with_logprobs=ui_logprobs_requested(),
            top_logprobs=int(get_config_value("logprobs.top_logprobs", default=5) or 5),
        ):
            yield _Event(
                content=content_chunk,
                reasoning=reasoning_chunk,
                tokens=tokens,
                reasoning_tokens=reasoning_tokens,
                lp=lp_items or {},
                prompt_tokens=prompt_tokens,
            )

    # ── per-chunk ingestion ──────────────────────────────────────────────────

    async def _ingest(self, event: _Event, state: _Attempt) -> bool:
        """Fold one event into the attempt state.

        Returns True when the stream must be aborted (degeneration, thinking loop,
        thinking-budget exhaustion) — the reason is recorded on ``state``.
        """
        session = self.session

        state.thinking_text += event.reasoning
        state.response_text += event.content
        state.pending_lp_r += event.lp.get("r") or []
        state.pending_lp_c += event.lp.get("c") or []
        state.lp_c_all += event.lp.get("c") or []

        if event.reasoning and state.thinking_start_wall is None:
            state.thinking_start_wall = time.time()
        if event.content and state.thinking_start_wall is not None:
            state.thinking_elapsed += time.time() - state.thinking_start_wall
            state.thinking_start_wall = None

        state.token_count = event.tokens
        session.token_count = state.start_tokens + event.tokens
        session.thinking_tokens = event.reasoning_tokens
        ui_update_thinking_tokens(session.request_id, event.reasoning_tokens)
        if event.prompt_tokens:
            state.prompt_tokens = max(state.prompt_tokens, int(event.prompt_tokens))

        # Live input/reasoning/answer estimates for the header token pill
        est_reasoning, est_answer = estimate_token_split(
            session.token_count,
            len(session.thinking_text or ""),
            len(self.accumulated_text) + len(state.response_text),
        )
        ui_update_token_counts(
            session.request_id,
            session.input_tokens + state.prompt_tokens,
            session.token_count,
            est_reasoning,
            est_answer,
        )

        if not state.first_token_seen and (
            event.content or event.reasoning or event.tokens > 0 or event.reasoning_tokens > 0
        ):
            session.ttft_seconds = time.time() - session.start_time
            state.first_token_seen = True
            ui_update_ttft(session.request_id, session.ttft_seconds)

        # Track the character count during the thinking phase (until real tokens arrive)
        if not state.response_text.strip() and event.reasoning:
            state.thinking_char_count = len(state.thinking_text)

        # Preserve thinking across recovery: only the first attempt defines it
        if self.attempt_index == 0:
            session.thinking_text = state.thinking_text

        if self._thinking_budget_reached(state):
            return True

        candidate_text = self.accumulated_text + state.response_text

        # ── thinking phase: reasoning exists but no content yet ──────────────
        if not state.response_text.strip() and state.thinking_text.strip():
            return await self._checkpoint_thinking(event, state)

        if not self.cp_manager.should_checkpoint(
            candidate_text, event.content, session.token_count,
            self.last_checkpoint_token, state.prev_cp_len,
        ):
            return False

        detection = self.cp_manager.health_check(candidate_text)
        if detection.severity == "critical" and session.checkpoints:
            # Abort early — the post-stream block performs the rollback
            state.aborted = detection
            return True
        if detection.severity != "critical":
            return await self._checkpoint_content(candidate_text, state, detection)
        return False

    def _thinking_budget_reached(self, state: _Attempt) -> bool:
        """Abort early instead of waiting for a stream that will never answer."""
        budget = self.context.thinking_budget_tokens
        if budget is None or state.response_text.strip() or not state.thinking_text.strip():
            return False
        estimate = len(state.thinking_text) / THINKING_CHARS_PER_TOKEN
        if estimate < budget * THINKING_BUDGET_EXHAUSTION_THRESHOLD:
            return False
        log.warning(
            f"[{self.session.request_id}] Thinking budget reached during streaming: "
            f"{len(state.thinking_text)} chars ≈ {estimate:.0f} tokens "
            f"(budget: {budget} tokens) — aborting stream early"
        )
        return True

    async def _checkpoint_thinking(self, event: _Event, state: _Attempt) -> bool:
        """Checkpoint reasoning on a paragraph break, and watch for a thinking loop.

        Returns True when a thinking loop was detected (the stream must stop).
        """
        thinking_delta = len(state.thinking_text) - state.prev_thinking_len
        thinking_budget = self.context.thinking_budget_tokens
        # Checkpoints are created when thinking was requested OR when thinking happens
        # unexpectedly (enable_thinking=True without an explicit budget leaves the
        # budget None, so the first condition alone would miss it).
        thinking_requested = (
            (thinking_budget is not None and thinking_budget > 0) or self.context.enable_thinking
        )
        if not (thinking_requested and "\n\n" in event.reasoning and thinking_delta > 0):
            return False

        session = self.session
        entropy = self.cp_manager.get_last_checkpoint_entropy(state.thinking_text)
        session.checkpoints.append(Checkpoint(
            token_position   = state.thinking_char_count,
            generated_text   = self.accumulated_text + state.thinking_text,
            metrics_snapshot = {"thinking_checkpoint": True},
            entropy          = entropy,
        ))

        # Show the thinking added since the last checkpoint, not the tail of all of it
        thinking_chunk = state.thinking_text[state.prev_thinking_len:]
        snippet = thinking_chunk[:THINKING_SNIPPET_CHARS]
        state.prev_thinking_len = len(state.thinking_text)
        self.last_checkpoint_token = state.thinking_char_count

        print_thinking_block("checkpoint", snippet)
        ui_add_checkpoint(
            session.request_id, "thinking",
            f"thinking @ CHAR- {state.thinking_char_count}{_entropy_suffix(entropy)}",
            snippet, entropy, token_lps=state.pending_lp_r,
        )
        state.pending_lp_r = []
        log.info(f"[{session.request_id}] Checkpoint saved (entropy={_entropy_display(entropy)})")

        global_cp_idx = len(session.checkpoints) - 1
        local_cp_idx = global_cp_idx - state.checkpoint_start
        context_text = (
            state.thinking_text[:state.prev_thinking_len - len(thinking_chunk)]
            if state.prev_thinking_len > 0 else ""
        )
        self.semantic.submit(
            local_cp_idx, thinking_chunk, context_text, global_cp_idx,
            ui_update_checkpoint_semantic,
        )

        self._detect_thinking_loop(thinking_chunk, state)
        if state.thinking_loop is not None:
            log.warning(
                f"[{session.request_id}] Thinking loop detected: {state.thinking_loop[0]} "
                f"at {len(state.thinking_text)} chars — aborting stream"
            )
            return True
        return False

    def _detect_thinking_loop(self, thinking_chunk: str, state: _Attempt) -> None:
        """Detect verbatim repeats, repetition-detector hits or verifier three-strike.

        Records how much thinking is worth keeping, so a retry can answer without
        redoing the reasoning that was still healthy.
        """
        state.thinking_cp_ends.append(len(state.thinking_text))

        normalized = " ".join(thinking_chunk.split())
        if len(normalized) >= THINKING_LOOP_MIN_CHARS:
            first_end, seen = state.chunk_seen.get(normalized, (len(state.thinking_text), 0))
            state.chunk_seen[normalized] = (first_end, seen + 1)
            if seen + 1 >= THINKING_LOOP_MAX_REPEATS:
                state.thinking_loop = (f"same thinking block repeated {seen + 1} times", first_end)

        if state.thinking_loop is None and self.cp_manager.repetition_critical(state.thinking_text):
            state.thinking_loop = (
                "repetition detector",
                state.thinking_cp_ends[max(0, len(state.thinking_cp_ends) - 4)],
            )

        if state.thinking_loop is None and self.semantic.rollback_triggered:
            keep_idx = min(
                self.semantic.rollback_local_idx or 0, len(state.thinking_cp_ends) - 1
            )
            state.thinking_loop = ("semantic verifier 3-strike", state.thinking_cp_ends[keep_idx])

    async def _checkpoint_content(
        self, candidate_text: str, state: _Attempt, detection: DetectionResult
    ) -> bool:
        """Commit a healthy checkpoint and fan it out to the UI, translator, verifier.

        Returns True when the verifier's three-strike rule aborts the stream.
        """
        session = self.session
        entropy = self.cp_manager.get_last_checkpoint_entropy(candidate_text)
        entropy_status = "ok"
        if entropy is not None and (entropy < ENTROPY_MIN_HEALTHY or entropy > ENTROPY_MAX_HEALTHY):
            entropy_status = "warning"

        session.checkpoints.append(Checkpoint(
            token_position   = session.token_count,
            generated_text   = candidate_text,
            metrics_snapshot = {"health_score": detection.score},
            entropy          = entropy,
            entropy_status   = entropy_status,
        ))
        self.cp_manager.increment_checkpoint_count()

        chunk_text = candidate_text[state.prev_cp_len:]
        state.prev_cp_len = len(candidate_text)
        self.last_checkpoint_token = session.token_count

        checkpoint_label = (
            f"checkpoint @ token {session.token_count}  "
            f"score={detection.score:.1f}{_entropy_suffix(entropy)}"
        )
        print_block(checkpoint_label, True, chunk_text)

        global_cp_idx = len(session.checkpoints) - 1
        ui_add_checkpoint(
            session.request_id, "ok", checkpoint_label, chunk_text, entropy,
            token_lps=state.pending_lp_c,
        )
        state.pending_lp_c = []
        log.info(f"[{session.request_id}] Checkpoint saved (entropy={_entropy_display(entropy)})")

        # Content checkpoints only — thinking is never translated
        if self.translation_svc is not None:
            content_type = detect_content_type(chunk_text)
            log.debug(
                f"[{session.request_id}] Submitting checkpoint {global_cp_idx} for translation "
                f"({len(chunk_text)} chars, type={content_type})"
            )
            self.translation_svc.submit(global_cp_idx, chunk_text, content_type)

        # Async semantic validation runs in parallel and never blocks the stream
        local_cp_idx = global_cp_idx - state.checkpoint_start
        context_text = candidate_text[:state.prev_cp_len - len(chunk_text)]
        self.semantic.submit(
            local_cp_idx, chunk_text, context_text, global_cp_idx,
            ui_update_checkpoint_semantic,
        )

        # A verifier result from an earlier checkpoint may already force a rollback
        if self.semantic.rollback_triggered:
            local_rollback = self.semantic.rollback_local_idx or 0
            global_rollback = state.checkpoint_start + local_rollback
            state.aborted = DetectionResult(
                score=0.0, severity="critical",
                reason=f"semantic 3-strike rollback to cp[{global_rollback}]",
            )
            log.warning(
                f"[{session.request_id}] Semantic rollback triggered mid-stream "
                f"→ cp[{global_rollback}]"
            )
            return True
        return False

    # ── after the stream ended ───────────────────────────────────────────────

    async def _post_stream(self, attempt: int, state: _Attempt) -> _Verdict:
        """Account for the attempt, then run the ordered recovery checks."""
        session = self.session
        log.warning(
            f"[{session.request_id}] POST-STREAM: thinking={len(state.thinking_text)} "
            f"response={len(state.response_text)} attempt={attempt}"
        )
        session.token_count = state.start_tokens + state.token_count
        session.input_tokens += state.prompt_tokens

        if state.thinking_text.strip():
            log.info(
                f"[{session.request_id}] Full thinking text (attempt {attempt + 1}):\n"
                f"{state.thinking_text}"
            )
        if state.thinking_start_wall is not None:
            state.thinking_elapsed += time.time() - state.thinking_start_wall
        session.thinking_phase_duration += state.thinking_elapsed
        if state.response_text.strip():
            log.info(
                f"[{session.request_id}] Full response text (attempt {attempt + 1}):\n"
                f"{state.response_text}"
            )

        self._preserve_thinking(attempt, state)
        self._checkpoint_unseen_thinking(attempt, state)

        if is_thinking_exhausted(state.thinking_text, state.response_text,
                                 self.context.thinking_budget_tokens):
            self.thinking_exhausted = True
            log.info(
                f"[{session.request_id}] Thinking budget exhausted: "
                f"{len(state.thinking_text)} chars of thinking consumed"
            )

        decision: Optional[_Verdict] = (
            await self._decide_aborted(state)
            or await self._decide_thinking_loop(state)
            or await self._decide_thinking_exhausted(state)
            or await self._decide_empty_after_thinking(state)
            or await self._decide_degenerated(state)
            or await self._decide_empty_final(state)
        )
        if decision is not None:
            return decision
        return await self._complete(attempt, state)

    def _preserve_thinking(self, attempt: int, state: _Attempt) -> None:
        """Keep the first attempt's reasoning, or adopt a later one if the first had none."""
        session = self.session
        if attempt == 0:
            self.initial_thinking_text = state.thinking_text
            session.thinking_text = state.thinking_text
            log.info(
                f"[{session.request_id}] Attempt 0: captured thinking_text "
                f"({len(state.thinking_text)} chars)"
            )
        elif state.thinking_text.strip() and not self.initial_thinking_text.strip():
            session.thinking_text = state.thinking_text
            log.info(
                f"[{session.request_id}] Attempt {attempt}: using new thinking from recovery "
                f"({len(state.thinking_text)} chars)"
            )
        elif self.initial_thinking_text.strip():
            session.thinking_text = self.initial_thinking_text
            log.info(
                f"[{session.request_id}] Attempt {attempt}: preserving original thinking "
                f"({len(session.thinking_text)} chars)"
            )

    def _checkpoint_unseen_thinking(self, attempt: int, state: _Attempt) -> None:
        """Broadcast thinking the stream never checkpointed, so retries show it too.

        Short tool-call thoughts never contain "\\n\\n", so mid-stream thinking
        checkpoints are never created — without this the reasoning would be
        invisible on every retry and failure.
        """
        session = self.session
        if not (state.thinking_text.strip() and self.context.enable_thinking):
            log.info(f"[{session.request_id}] Attempt {attempt}: no thinking to preserve")
            return

        scoped = session.checkpoints[state.checkpoint_start:]
        has_thinking_checkpoint = any(
            getattr(cp, "metrics_snapshot", {}).get("thinking_checkpoint", False) for cp in scoped
        )
        if has_thinking_checkpoint:
            return

        snippet = state.thinking_text[:THINKING_SNIPPET_CHARS]
        session.checkpoints.insert(state.checkpoint_start, Checkpoint(
            token_position   = len(state.thinking_text),
            generated_text   = self.accumulated_text,
            metrics_snapshot = {"thinking_checkpoint": True},
        ))
        ui_add_checkpoint(
            session.request_id, "thinking",
            f"thinking @ CHAR-{len(state.thinking_text)} (attempt {attempt + 1})", snippet,
        )
        log.info(
            f"[{session.request_id}] Early thinking checkpoint added "
            f"(attempt {attempt + 1}, {len(state.thinking_text)} chars)"
        )
        self.semantic.submit(0, snippet, "", state.checkpoint_start, ui_update_checkpoint_semantic)

    def _thinking_tokens_emitted(self, state: _Attempt) -> int:
        return int(len((state.thinking_text or "").strip()) / THINKING_CHARS_PER_TOKEN)

    def _nothink_request(self, state: _Attempt) -> dict:
        """Retry request: prior reasoning injected as context, thinking switched off."""
        return build_nothink_request(
            self.context.request_data, self.session.messages, state.thinking_text
        )

    # ── ordered recovery checks ──────────────────────────────────────────────

    async def _decide_aborted(self, state: _Attempt) -> Optional[_Verdict]:
        """1. Degeneration detected mid-stream: roll back to a healthy checkpoint."""
        if state.aborted is None:
            return None
        session = self.session

        if "semantic 3-strike" in state.aborted.reason and self.semantic.rollback_triggered:
            local_rollback = self.semantic.rollback_local_idx or 0
            global_rollback = state.checkpoint_start + local_rollback
            rollback_cp = (
                session.checkpoints[global_rollback]
                if global_rollback < len(session.checkpoints) else session.checkpoints[-1]
            )
            detector_name = "SemanticProgressDetector"
        else:
            rollback_cp = session.checkpoints[-1]
            detector_name = "RepetitionDetector"

        label = (
            f"degeneration ({detector_name}) score={state.aborted.score:.1f} "
            f"— rolling back to token {rollback_cp.token_position}"
        )
        print_block(label, False, state.response_text, note=self._attempt_label)
        log.warning(
            f"[{session.request_id}] Degeneration detected mid-stream ({detector_name}, "
            f"score={state.aborted.score:.1f}). Rolling back to checkpoint. "
            f"{self._attempt_label}"
        )
        log_telemetry(session, rollback_cp.token_position, detector_name,
                      state.aborted.score, "rollback")
        session.rejected_checkpoints_count += 1
        session.rejected_checkpoints_tokens += len(state.response_text.split())
        self.accumulated_text = rollback_cp.generated_text
        self.current_request = self._continuation_request(self.accumulated_text)
        session.recovery_attempts += 1
        self._restart_translation_service()
        ui_add_checkpoint(session.request_id, "reject", label,
                          state.response_text[:REJECT_EXCERPT_CHARS])
        return _Verdict(_Action.RETRY_ATTEMPT)

    async def _decide_thinking_loop(self, state: _Attempt) -> Optional[_Verdict]:
        """1b. Keep the reasoning up to where it looped, then answer without thinking."""
        if state.thinking_loop is None:
            return None
        session = self.session
        loop_reason, keep_chars = state.thinking_loop
        dropped = len(state.thinking_text) - keep_chars
        state.thinking_text = state.thinking_text[:keep_chars]
        self.initial_thinking_text = state.thinking_text
        session.thinking_text = state.thinking_text

        detail = (
            f"thinking loop ({loop_reason}) — dropped {dropped} chars, "
            f"retrying without thinking"
        )
        excerpt = state.thinking_text[-REJECT_EXCERPT_TAIL:]
        print_block(detail, False, excerpt, note=self._attempt_label)
        ui_add_checkpoint(session.request_id, "reject", detail, excerpt)
        log.warning(f"[{session.request_id}] {detail}. {self._attempt_label}")
        log_telemetry(session, session.token_count, "ThinkingLoop", 0.0, "nothink_retry")
        session.rejected_checkpoints_count += 1
        session.should_log_interaction = True
        self.thinking_exhausted = True
        # A triggered rollback flag would otherwise abort the retry's first checkpoint
        self._restart_semantic_detector()
        self.current_request = self._nothink_request(state)
        session.recovery_attempts += 1
        return _Verdict(_Action.RETRY_ATTEMPT)

    async def _decide_thinking_exhausted(self, state: _Attempt) -> Optional[_Verdict]:
        """2. Reasoning filled the budget and no answer arrived → think no further."""
        if not is_thinking_exhausted(state.thinking_text, state.response_text,
                                     self.context.thinking_budget_tokens):
            return None
        session = self.session
        request_data = self.context.request_data
        budget = self.context.thinking_budget_tokens
        emitted = self._thinking_tokens_emitted(state)
        thinking_was_requested = (
            self.context.enable_thinking or (budget is not None and budget > 0)
        )
        if thinking_was_requested:
            session.should_log_interaction = True

        if state.nothink and thinking_was_requested:
            detail = (
                f"model kept thinking despite no-think retry "
                f"({emitted} tokens emitted, no answer) — retrying"
            )
        elif not thinking_was_requested:
            detail = (
                f"thinking disabled — model produced thinking when not requested "
                f"({emitted} tokens emitted)"
            )
        elif budget:
            detail = (
                f"thinking budget exhausted ({emitted}/{budget} tokens) "
                f"— retrying without thinking"
            )
        else:
            detail = (
                f"thinking consumed the whole token limit "
                f"({emitted} tokens, no answer) — retrying without thinking"
            )

        print_block(detail, False, state.thinking_text, note=self._attempt_label)
        ui_add_checkpoint(session.request_id, "reject", detail,
                          (state.thinking_text or "")[-REJECT_EXCERPT_TAIL:])
        log.warning(
            f"[{session.request_id}] {detail}. Switching to no-think retry. "
            f"{self._attempt_label}"
        )
        log_telemetry(session, session.token_count, "ThinkingMonitor", 0.0, "nothink_retry")

        # Inject the reasoning as context and retry with thinking disabled; give the
        # request a little more room, since reading the injected thinking costs tokens.
        original_max = request_data.get("max_tokens", DEFAULT_MAX_TOKENS)
        new_max = max(original_max, min(original_max + 4000, MAX_TOKENS_CEILING))
        self.current_request = self._nothink_request(state)
        self.current_request["max_tokens"] = new_max
        self.current_request["max_completion_tokens"] = new_max
        session.recovery_attempts += 1
        return _Verdict(_Action.RETRY_ATTEMPT)

    async def _decide_empty_after_thinking(self, state: _Attempt) -> Optional[_Verdict]:
        """3. Thinking with no answer is wrong by definition — always retry."""
        if not (state.thinking_text.strip() and not state.response_text.strip()):
            return None
        session = self.session
        print_block("empty content after thinking — must retry", False, state.response_text,
                    note=self._attempt_label)
        ui_add_checkpoint(session.request_id, "reject",
                          "empty content after thinking — must retry",
                          state.response_text[:REJECT_EXCERPT_CHARS])
        log.warning(
            f"[{session.request_id}] Empty content after thinking "
            f"(char_count={len(state.thinking_text)} thinking chars, 0 response chars). "
            f"Retrying. {self._attempt_label}"
        )
        session.rejected_checkpoints_count += 1
        self.current_request = self._nothink_request(state)
        session.recovery_attempts += 1
        return _Verdict(_Action.RETRY_ATTEMPT)

    async def _decide_degenerated(self, state: _Attempt) -> Optional[_Verdict]:
        """4. End-of-stream health check. Critical plus a checkpoint means roll back."""
        session = self.session
        state.candidate_text = self.accumulated_text + state.response_text
        state.detection = self.cp_manager.health_check(state.candidate_text)
        log.info(
            f"[{session.request_id}] Health score={state.detection.score:.1f} "
            f"({state.detection.severity}) — {state.detection.reason}"
        )
        if state.detection.severity != "critical":
            return None

        if session.checkpoints:
            last_cp = session.checkpoints[-1]
            label = (
                f"degeneration score={state.detection.score:.1f} "
                f"— rolling back to token {last_cp.token_position}"
            )
            print_block(label, False, state.response_text, note=self._attempt_label)
            ui_add_checkpoint(session.request_id, "reject", label,
                              state.response_text[:REJECT_EXCERPT_CHARS])
            log.warning(
                f"[{session.request_id}] Degeneration detected at end of stream "
                f"(score={state.detection.score:.1f}). Rolling back to checkpoint. "
                f"{self._attempt_label}"
            )
            log_telemetry(session, last_cp.token_position, "RepetitionDetector",
                          state.detection.score, "rollback")
            session.rejected_checkpoints_count += 1
            session.rejected_checkpoints_tokens += len(state.response_text.split())
            self.accumulated_text = last_cp.generated_text
            self.current_request = self._continuation_request(self.accumulated_text)
            session.recovery_attempts += 1
            return _Verdict(_Action.RETRY_ATTEMPT)

        # Degenerated but nothing healthy to roll back to: the best effort available
        # is the text we already have, so fall through to finishing instead of retrying.
        no_checkpoint_label = (
            f"degeneration score={state.detection.score:.1f} "
            f"— no checkpoint, returning best output"
        )
        print_block(no_checkpoint_label, False, state.response_text)
        ui_add_checkpoint(session.request_id, "error", no_checkpoint_label,
                          state.response_text[:REJECT_EXCERPT_CHARS])
        log.warning(
            f"[{session.request_id}] Degeneration detected but no checkpoint available. "
            f"Returning best available output."
        )
        return None

    async def _decide_empty_final(self, state: _Attempt) -> Optional[_Verdict]:
        """5. Nothing usable came back at all."""
        if state.candidate_text.strip():
            return None
        session = self.session
        print_block("empty final output — must retry", False, state.response_text,
                    note=self._attempt_label)
        ui_add_checkpoint(session.request_id, "reject", "empty final output — must retry",
                          state.response_text[:REJECT_EXCERPT_CHARS])
        log.warning(f"[{session.request_id}] Empty final output. Retrying. {self._attempt_label}")
        session.rejected_checkpoints_count += 1
        # Nothing came back at all: resend the original request, unless the model
        # already burned a thinking phase — then keep the reasoning and drop thinking.
        self.current_request = (
            self._original_request() if not state.thinking_text.strip()
            else self._nothink_request(state)
        )
        session.recovery_attempts += 1
        return _Verdict(_Action.RETRY_ATTEMPT)

    # ── success ──────────────────────────────────────────────────────────────

    async def _complete(self, attempt: int, state: _Attempt) -> _Verdict:
        """Accept the generated text and finish."""
        session = self.session
        detection = state.detection or DetectionResult(score=100.0, severity="ok", reason="accepted")
        self.accumulated_text = state.candidate_text
        label = (
            f"generation complete  attempts={attempt + 1}  "
            f"checkpoints={len(session.checkpoints)}  score={detection.score:.1f}"
        )
        print_block(label, True, state.response_text)

        self._consolidate_thinking(state)

        # If the last checkpoint already holds exactly this text, relabel it in place
        # instead of adding a duplicate: the client received the original checkpoint
        # over SSE already, so popping and re-adding would show two entries.
        if session.checkpoints:
            last_cp = session.checkpoints[-1]
            if getattr(last_cp, "generated_text", "").strip() == state.candidate_text.strip():
                interaction = _ui_find(session.request_id)
                if interaction and interaction["checkpoints"]:
                    interaction["checkpoints"][-1]["label"] = label
                    _ui_broadcast({
                        "type": "relabel_last_checkpoint",
                        "request_id": session.request_id,
                        "label": label,
                    })
                log.info(
                    f"[{session.request_id}] Generation complete "
                    f"(attempts={attempt + 1}, checkpoints={len(session.checkpoints)}) "
                    f"— relabeled last checkpoint"
                )
                return _Verdict(_Action.FINISH, self.accumulated_text)

        ui_add_checkpoint(
            session.request_id, "ok",
            f"generation complete  attempts={attempt + 1}  "
            f"checkpoints={len(session.checkpoints) + 1}  score={detection.score:.1f}",
            state.response_text[:REJECT_EXCERPT_CHARS],
            token_lps=clip_logprobs(state.lp_c_all, REJECT_EXCERPT_CHARS),
        )
        log.info(
            f"[{session.request_id}] Generation complete "
            f"(attempts={attempt + 1}, checkpoints={len(session.checkpoints)})"
        )
        return _Verdict(_Action.FINISH, self.accumulated_text)

    def _consolidate_thinking(self, state: _Attempt) -> None:
        """Add a single thinking checkpoint when the stream never produced one.

        Covers requests with thinking enabled whose reasoning has no intermediate
        "\\n\\n" boundary to checkpoint on.
        """
        session = self.session
        if not (state.thinking_text.strip() and self.context.enable_thinking):
            return
        if any(getattr(cp, "metrics_snapshot", {}).get("thinking_checkpoint", False)
               for cp in session.checkpoints):
            return

        snippet = state.thinking_text[:THINKING_SNIPPET_CHARS]
        session.checkpoints.insert(0, Checkpoint(
            token_position   = len(state.thinking_text),
            generated_text   = self.accumulated_text,
            metrics_snapshot = {"thinking_checkpoint": True},
        ))
        print_thinking_block("checkpoint (consolidated)", snippet)
        ui_add_checkpoint(
            session.request_id, "thinking",
            f"thinking @ CHAR- {len(state.thinking_text)} (consolidated)", snippet,
        )
        log.info(
            f"[{session.request_id}] Consolidated thinking checkpoint added "
            f"(thinking_len={len(state.thinking_text)} chars)"
        )
        self.semantic.submit(0, snippet, "", 0, ui_update_checkpoint_semantic)


async def supervise_generation(
    request_data: dict,
    request: Optional[object] = None,
    request_id: Optional[str] = None,
) -> Tuple[dict, GenerationSession]:
    """Parse ``request_data`` and supervise the resulting generation.

    The HTTP layer's single seam into the supervisor — also the point tests replace.
    """
    context = parse_request(request_data)
    supervisor = GenerationSupervisor(
        context,
        request_id=request_id,
        request=request,
        authorization=getattr(request, "headers", {}).get("authorization")
        if request is not None else None,
    )
    return await supervisor.run()
