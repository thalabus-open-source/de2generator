"""Supervision scenarios driven by a scripted backend stream.

Every test replaces ``orchestration.supervisor.stream_from_vllm`` with a fake
backend and the semantic verifier with an inert recorder, so the whole state
machine — checkpointing, the ordered recovery checks, retry request rewriting,
token accounting and response assembly — runs in-process and deterministically:
no GPU, no network, no verifier model.

Run: python -m unittest src.test.test_supervision_scenarios   (or pytest)
"""

import sys
import unittest
import uuid
from pathlib import Path
from typing import Optional
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import orchestration.supervisor as supervisor_module                     # noqa: E402
from orchestration.request_context import parse_request                   # noqa: E402
from orchestration.supervisor import GenerationSupervisor                # noqa: E402
from ui import _ui_find                                                  # noqa: E402

# ── scripted payloads ───────────────────────────────────────────────────────

HEALTHY = "The harbour master filed the log."
ANSWER = (
    "The lighthouse keeper logged every passing ship.\n\n"
    "Each night he climbed the ninety steps, and each dawn he wrote the same summary.\n\n"
    "That routine was the reason no wreck happened on his watch."
)
DEGENERATE_BLOCK = "aaaa bbbb cccc dddd eeee " * 6     # ~150 chars, entropy ≈ 1.9
THOUGHT = "The keeper checked the tide tables and the fuel reserve."
LOOP_BLOCK = (
    "The keeper must weigh the tide tables, the fuel reserve, the relay schedule "
    "and the visibility forecast again."
)


def content(text: str, tokens: int = 40, prompts: int = 25) -> tuple:
    """(content, reasoning, completion_tokens, reasoning_tokens, logprobs, prompt_tokens)"""
    return (text, "", tokens, 0, {}, prompts)


def reasoning(text: str, tokens: int = 40, reasoning_tokens: int = 40) -> tuple:
    return ("", text, tokens, reasoning_tokens, {}, 25)


def finish(tokens: int, reasoning_tokens: int = 0, prompts: int = 25) -> tuple:
    """Trailing usage-only event: exact counters, empty delta, must not checkpoint."""
    return ("", "", tokens, reasoning_tokens, {}, prompts)


def degenerate_stream() -> list[tuple]:
    """One healthy checkpoint, then three low-entropy checkpoints (three strikes)."""
    return [
        content(HEALTHY),
        content("\n\n" + DEGENERATE_BLOCK),
        content("\n\n" + DEGENERATE_BLOCK),
        content("\n\n" + DEGENERATE_BLOCK, tokens=600),
    ]


def paragraph_events(text: str) -> list[tuple]:
    """Stream one event per paragraph, as a real model would."""
    parts = text.split("\n\n")
    return [
        content(part + ("\n\n" if index < len(parts) - 1 else ""), tokens=40 * (index + 1))
        for index, part in enumerate(parts)
    ]


class FakeSemantic:
    """Stand-in for the async verifier: records submissions, no HTTP, no threads."""

    def __init__(self, request_id: str) -> None:
        self.submitted: list[tuple] = []
        self.shutdown_calls = 0
        self.rollback_triggered = False
        self.rollback_local_idx: Optional[int] = None

    def submit(self, local_idx, block, context, global_idx, callback) -> None:
        self.submitted.append((local_idx, block, context, global_idx))

    def shutdown(self) -> None:
        self.shutdown_calls += 1


class RollbackOnSecondCheckpoint(FakeSemantic):
    """Verifier whose three-strike rule fires exactly once per test run.

    Latching matters: the supervisor deliberately keeps the flagged verifier for
    the retry (only a full restart rebuilds it), so an unlatched stand-in would
    abort every attempt and say nothing about the recovery path under test.
    """

    fired = False

    def submit(self, local_idx, block, context, global_idx, callback) -> None:
        super().submit(local_idx, block, context, global_idx, callback)
        if not RollbackOnSecondCheckpoint.fired and local_idx >= 1:
            RollbackOnSecondCheckpoint.fired = True
            self.rollback_triggered = True
            self.rollback_local_idx = 0


class DisconnectAfter:
    """Fake ASGI request whose client disappears after ``after`` checks."""

    def __init__(self, after: int = 1) -> None:
        self.after = after
        self.checked = 0

    async def is_disconnected(self) -> bool:
        self.checked += 1
        return self.checked > self.after


def request_body(model: str = "test-model", **overrides) -> dict:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Write a short log entry."}],
        "max_tokens": 8192,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    body.update(overrides)
    return body


class SupervisionScenarioTests(unittest.IsolatedAsyncioTestCase):
    """The supervisor's decision table, one scenario per test."""

    def setUp(self) -> None:
        self.created_detectors: list[FakeSemantic] = []
        self._patches = []
        self._start(
            patch.object(supervisor_module, "SemanticProgressDetector",
                         self._detector_factory),
            patch.object(supervisor_module, "MAX_RECOVERY_ATTEMPTS", 1, create=True),
            patch.object(supervisor_module, "MAX_FULL_RESTARTS", 0, create=True),
        )

    def tearDown(self) -> None:
        for patcher in self._patches:
            patcher.stop()

    def _start(self, *patchers):
        for patcher in patchers:
            patcher.start()
            self._patches.append(patcher)

    def _detector_factory(self, request_id: str) -> FakeSemantic:
        detector = self.detector_class(request_id)
        self.created_detectors.append(detector)
        return detector

    detector_class = FakeSemantic

    async def _run(self, attempts: list[list[tuple]], *, body: Optional[dict] = None,
                   request=None, max_attempts: int = 1, max_restarts: int = 0):
        """Supervise one generation against a scripted backend.

        Returns ``(response, session, requests, endpoints)`` where ``requests`` /
        ``endpoints`` hold what the supervisor actually sent upstream, per attempt.
        """
        requests: list[dict] = []
        endpoints: list[Optional[str]] = []
        state = {"count": 0}

        def fake_stream(request_data, session_id, vllm_endpoint=None, authorization=None,
                        with_logprobs=False, top_logprobs=5):
            async def generate():
                index = min(state["count"], len(attempts) - 1)
                state["count"] += 1
                requests.append(dict(request_data))
                endpoints.append(vllm_endpoint)
                counters = (0, 0, 25)
                for event in attempts[index]:
                    counters = (event[2], event[3], event[5])
                    yield event
                # Backends always close a stream with a usage event; repeat the
                # counters instead of inventing one, so accounting stays honest.
                yield finish(*counters)
            return generate()

        self._start(
            patch.object(supervisor_module, "stream_from_vllm", fake_stream),
            patch.object(supervisor_module, "MAX_RECOVERY_ATTEMPTS", max_attempts, create=True),
            patch.object(supervisor_module, "MAX_FULL_RESTARTS", max_restarts, create=True),
        )

        context = parse_request(body or request_body(),
                                get_value=lambda key_path, env_var=None, default=None: default)
        supervisor = GenerationSupervisor(context, request_id=f"unit-{uuid.uuid4()}")
        if request is not None:
            supervisor.request = request
        response, session = await supervisor.run()
        return response, session, requests, endpoints

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _content_of(response: dict) -> str:
        return response["choices"][0]["message"]["content"]

    @staticmethod
    def _last_message(request: dict) -> str:
        return (request.get("messages") or [{}])[-1].get("content", "")

    # ── happy paths ─────────────────────────────────────────────────────────

    async def test_plain_answer_completes_without_recovery(self) -> None:
        response, session, requests, _ = await self._run([[content(ANSWER)]])
        self.assertEqual(len(requests), 1)
        self.assertEqual(self._content_of(response), ANSWER)
        self.assertEqual(response["de2generator"]["recovery_attempts"], 0)
        self.assertEqual(session.recovery_attempts, 0)
        self.assertEqual(response["de2generator"]["truncation_status"], "no_truncation")
        self.assertEqual(self.created_detectors[-1].rollback_triggered, False)

    async def test_paragraph_boundaries_produce_checkpoints(self) -> None:
        events = paragraph_events(ANSWER)
        self.assertGreaterEqual(len(events), 2)
        response, session, _, _ = await self._run([events])
        checkpoints = [cp for cp in session.checkpoints
                       if not cp.metrics_snapshot.get("thinking_checkpoint")]
        self.assertGreaterEqual(len(checkpoints), 2)
        # Checkpoints grow monotonically and every one is a prefix of the answer
        texts = [cp.generated_text for cp in checkpoints]
        self.assertTrue(all(b.startswith(a) for a, b in zip(texts, texts[1:])), texts)
        self.assertTrue(all(text in ANSWER for text in texts), texts)
        self.assertEqual(self._content_of(response), ANSWER,
                         "the unccheckpointed tail still reaches the client")

    async def test_usage_event_alone_never_creates_a_checkpoint(self) -> None:
        """The closing usage chunk jumps the token counter but carries no text."""
        _, session, _, _ = await self._run(
            [[content("A complete sentence.", tokens=300), finish(5000)]])
        checkpoints = [cp for cp in session.checkpoints
                       if not cp.metrics_snapshot.get("thinking_checkpoint")]
        self.assertEqual(len(checkpoints), 1)
        self.assertEqual(checkpoints[0].generated_text, "A complete sentence.")

    async def test_backend_counters_drive_the_usage_block(self) -> None:
        response, _, _, _ = await self._run(
            [[content("Short answer.", tokens=31, prompts=44)]])
        usage = response["usage"]
        self.assertEqual(usage["prompt_tokens"], 44)
        self.assertEqual(usage["completion_tokens"], 31)
        self.assertEqual(usage["total_tokens"], 75)
        stats = response["de2generator"]
        self.assertEqual(stats["input_tokens"], 44)
        self.assertEqual(stats["reasoning_tokens_est"] + stats["answer_tokens_est"], 31)

    async def test_thinking_is_checkpointed_streamed_and_returned(self) -> None:
        response, session, _, _ = await self._run(
            [[reasoning(THOUGHT + "\n\n", tokens=20, reasoning_tokens=20),
              content(ANSWER), finish(60, 20)]])
        message = response["choices"][0]["message"]
        self.assertTrue(message["reasoning_content"].startswith(THOUGHT))
        self.assertEqual(session.thinking_tokens, 20)
        self.assertEqual(response["de2generator"]["thinking_tokens"], 20)
        thinking = [cp for cp in session.checkpoints
                    if cp.metrics_snapshot.get("thinking_checkpoint")]
        self.assertTrue(thinking, "reasoning should be checkpointed")
        self.assertTrue(self.created_detectors[0].submitted,
                        "reasoning should be submitted for semantic validation")

    async def test_extension_block_never_reaches_the_backend(self) -> None:
        body = request_body(de2generator={"model_endpoint": "http://other:9000/v1",
                                          "agent_name": "AgentAssistant"})
        _, _, requests, endpoints = await self._run([[content(ANSWER)]], body=body)
        self.assertNotIn("de2generator", requests[0])
        self.assertEqual(endpoints[0], "http://other:9000")

    # ── degeneration: rollback, continuation, give up ───────────────────────

    async def test_repeated_low_entropy_checkpoints_abort_the_stream(self) -> None:
        response, session, requests, _ = await self._run(
            [degenerate_stream(), [content(ANSWER), finish(500)]])

        self.assertEqual(len(requests), 2, "a second attempt was made")
        self.assertEqual(session.recovery_attempts, 1)
        self.assertEqual(session.rejected_checkpoints_count, 1)
        self.assertEqual(response["de2generator"]["recovery_attempts"], 1)

        continuation = requests[1]["messages"][-1]["content"]
        self.assertIn("Continue from the following text", continuation)
        self.assertIn(HEALTHY, continuation, "accepted text is fed back")
        self.assertIn(HEALTHY, self._content_of(response))
        self.assertIn(ANSWER, self._content_of(response))

    async def test_rollback_keeps_text_committed_before_the_strikes(self) -> None:
        """Strikes 1 and 2 still commit checkpoints, so the rollback point is the
        last thing that was accepted — the third strike only stops the stream."""
        _, session, requests, _ = await self._run(
            [degenerate_stream(), [content(ANSWER), finish(900)]])
        committed = session.checkpoints
        self.assertGreaterEqual(len(committed), 2)
        self.assertTrue(requests[1]["messages"][-1]["content"].startswith(
            "Continue from the following text"))
        self.assertTrue(committed[-1].generated_text.startswith(HEALTHY))

    async def test_semantic_three_strike_rolls_back_where_the_verifier_says(self) -> None:
        """Verifier three-strike pins the rollback; only a restart clears the flag."""
        self.detector_class = RollbackOnSecondCheckpoint
        RollbackOnSecondCheckpoint.fired = False
        response, session, requests, _ = await self._run(
            [paragraph_events(ANSWER)], max_attempts=1, max_restarts=1)

        # Attempt 1 rejects, attempt 2 rejects again (the flagged verifier is
        # deliberately kept for the retry), so a full restart clears it
        self.assertEqual(len(requests), 3)
        self.assertIn("Continue from the following text",
                      requests[1]["messages"][-1]["content"])
        self.assertNotIn("Continue from the following text",
                         requests[2]["messages"][-1]["content"])
        self.assertEqual(len(self.created_detectors), 2, "one verifier per restart")
        self.assertTrue(self.created_detectors[0].rollback_triggered)
        self.assertFalse(self.created_detectors[-1].rollback_triggered,
                         "a restart starts a clean three-strike count")
        self.assertEqual(session.recovery_attempts, 0, "restarts clear the counter")
        self.assertIn(ANSWER, self._content_of(response))

    async def test_all_attempts_exhausted_returns_best_output(self) -> None:
        response, session, requests, _ = await self._run(
            [[finish(0)]], max_attempts=1, max_restarts=0)
        self.assertEqual(len(requests), 2)
        self.assertEqual(session.recovery_attempts, 2)
        self.assertEqual(self._content_of(response), "")

    async def test_full_restart_restarts_from_the_original_request(self) -> None:
        response, session, requests, _ = await self._run(
            [degenerate_stream(), degenerate_stream(), [content(ANSWER), finish(900)]],
            max_attempts=0, max_restarts=2)
        # one attempt per restart, three restarts
        self.assertEqual(len(requests), 3)
        last_message = requests[2]["messages"][-1]["content"]
        self.assertNotIn("Continue from the following text", last_message,
                         "a restart must not continue discarded output")
        self.assertIn(ANSWER, self._content_of(response))
        self.assertEqual(session.recovery_attempts, 0, "restarts clear the attempt counter")

    # ── thinking failures ───────────────────────────────────────────────────

    async def test_thinking_without_an_answer_retries_without_thinking(self) -> None:
        response, session, requests, _ = await self._run(
            [[reasoning(THOUGHT, tokens=200, reasoning_tokens=200)],
             [content(ANSWER), finish(300, 200)]])

        self.assertEqual(len(requests), 2)
        self.assertEqual(session.recovery_attempts, 1)
        self.assertTrue(session.should_log_interaction, "overthinking is audited")

        retry = requests[1]
        self.assertIs(retry["chat_template_kwargs"]["enable_thinking"], False)
        self.assertNotIn("thinking_budget", retry["chat_template_kwargs"])
        joined = " ".join(m.get("content", "") for m in retry["messages"])
        self.assertIn("[PREVIOUS INTERRUPTED REASONING]", joined)
        self.assertIn("/no_think", joined)
        self.assertEqual(self._content_of(response), ANSWER)
        self.assertEqual(response["de2generator"]["truncation_status"],
                         "thinking_budget_exhausted_but_recovered")

    async def test_nothink_retry_is_given_more_room(self) -> None:
        _, _, requests, _ = await self._run(
            [[reasoning(THOUGHT, tokens=200, reasoning_tokens=200)],
             [content(ANSWER), finish(300, 200)]],
            body=request_body(max_tokens=8192))
        self.assertEqual(requests[1]["max_tokens"], 12192)
        self.assertEqual(requests[1]["max_completion_tokens"], 12192)

    async def test_budget_exhaustion_aborts_the_stream_early(self) -> None:
        """A budgeted request stops streaming once the budget is mathematically gone."""
        body = request_body(chat_template_kwargs={"enable_thinking": True,
                                                 "thinking_budget": 50})
        chatty = [reasoning(THOUGHT + " " * 200, tokens=400 * (i + 1),
                           reasoning_tokens=400 * (i + 1)) for i in range(4)]
        _, _, requests, _ = await self._run([chatty, [content(ANSWER), finish(900, 20)]],
                                           body=body)
        self.assertEqual(len(requests), 2)
        self.assertIs(requests[1]["chat_template_kwargs"]["enable_thinking"], False)

    async def test_repeated_thinking_block_is_cut_and_answered_without_thinking(self) -> None:
        looping = [reasoning(LOOP_BLOCK + "\n\n", tokens=100 * (i + 1),
                            reasoning_tokens=100 * (i + 1)) for i in range(5)]
        response, session, requests, _ = await self._run(
            [looping, [content(ANSWER), finish(900, 200)]])

        self.assertEqual(len(requests), 2)
        self.assertEqual(session.recovery_attempts, 1)
        self.assertTrue(session.should_log_interaction)
        self.assertLess(len(session.thinking_text), len(LOOP_BLOCK) * 5,
                        "the looped part is dropped, the useful prefix is kept")
        self.assertIs(requests[1]["chat_template_kwargs"]["enable_thinking"], False)
        self.assertEqual(response["de2generator"]["truncation_status"],
                         "thinking_budget_exhausted_but_recovered")

    async def test_unrequested_thinking_keeps_the_answer(self) -> None:
        """enable_thinking=False yet the model thinks anyway: an answer is an answer."""
        body = request_body(chat_template_kwargs={"enable_thinking": False})
        response, session, requests, _ = await self._run(
            [[reasoning(THOUGHT, tokens=10, reasoning_tokens=10),
              content(ANSWER), finish(50, 10)]], body=body)
        self.assertEqual(len(requests), 1)
        self.assertEqual(session.recovery_attempts, 0)
        self.assertEqual(self._content_of(response), ANSWER)

    # ── client behaviour and dashboards ─────────────────────────────────────

    async def test_client_disconnect_abandons_the_stream(self) -> None:
        response, session, requests, _ = await self._run(
            [[content("Enough for a valid answer."), content(" Never delivered.")]],
            request=DisconnectAfter(1))
        self.assertEqual(session.recovery_attempts, 0)
        self.assertNotIn("Never delivered", self._content_of(response))

    async def test_dashboard_receives_checkpoints(self) -> None:
        response, _, _, _ = await self._run([[content(ANSWER)]])
        request_id = response["de2generator"]["request_id"]
        interaction = _ui_find(request_id)
        self.assertIsNotNone(interaction)
        self.assertEqual(interaction["model"], "test-model")
        self.assertTrue(interaction["checkpoints"], "the UI saw checkpoints")
        self.assertTrue(any(cp["kind"] == "ok" for cp in interaction["checkpoints"]))


if __name__ == "__main__":
    unittest.main()
