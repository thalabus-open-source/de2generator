"""Response assembly, token accounting and translation merging.

Run: pytest src/test/test_response_builder.py
"""

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core.models import Checkpoint, GenerationSession                      # noqa: E402
from response.builder import build_response, clip_logprobs, estimate_token_split  # noqa: E402
from translation.merging import (                                          # noqa: E402
    content_checkpoint_indices,
    merge_overlapping_translations,
    merged_translation,
)


def _session(**overrides) -> GenerationSession:
    session = GenerationSession(
        request_id="req-1",
        model="test-model",
        messages=[{"role": "user", "content": "hi"}],
        token_count=100,
        thinking_tokens=40,
        input_tokens=25,
    )
    for key, value in overrides.items():
        setattr(session, key, value)
    return session


def _content_checkpoint(text: str) -> Checkpoint:
    return Checkpoint(token_position=1, generated_text=text, metrics_snapshot={"health_score": 99.0})


def _thinking_checkpoint(text: str) -> Checkpoint:
    return Checkpoint(token_position=1, generated_text="",
                      metrics_snapshot={"thinking_checkpoint": True})


# ── token accounting ─────────────────────────────────────────────────────────

def test_estimate_split_always_sums_to_the_reported_total() -> None:
    for total, reasoning, answer in ((100, 300, 100), (100, 0, 500), (7, 1, 1), (0, 10, 10)):
        est_reasoning, est_answer = estimate_token_split(total, reasoning, answer)
        assert est_reasoning + est_answer == total, (total, reasoning, answer)


def test_estimate_split_never_goes_negative_or_over() -> None:
    assert estimate_token_split(-5, 10, 10) == (0, 0)
    assert estimate_token_split(50, 0, 0) == (0, 50)          # no text to reason from
    reasoning, answer = estimate_token_split(10, 10 ** 6, 1)
    assert reasoning == 10 and answer == 0


def test_build_response_reports_consistent_usage() -> None:
    session = _session(thinking_text="", )
    response = build_response(session, "hello world")
    usage = response["usage"]
    assert usage["prompt_tokens"] == 25
    assert usage["completion_tokens"] == 100
    assert usage["total_tokens"] == 125
    de2gen = response["de2generator"]
    assert de2gen["reasoning_tokens_est"] + de2gen["answer_tokens_est"] == 100
    assert de2gen["output_tokens"] == 100 - 40                # thinking subtracted
    assert de2gen["thinking_tokens"] == 40
    assert de2gen["request_id"] == "req-1"
    assert response["choices"][0]["message"]["content"] == "hello world"
    assert "reasoning_content" not in response["choices"][0]["message"]
    assert response["id"] == "chatcmpl-req-1"
    assert response["object"] == "chat.completion"
    assert response["choices"][0]["finish_reason"] == "stop"


def test_build_response_stores_accounting_on_the_session() -> None:
    session = _session()
    build_response(session, "answer")
    assert session.output_tokens == 60
    assert session.reasoning_tokens == 40
    assert session.truncation_status == "no_truncation"


def test_thinking_is_returned_in_the_openai_reasoning_field() -> None:
    session = _session(thinking_text="  I thought about it  ")
    message = build_response(session, "answer")["choices"][0]["message"]
    assert message["reasoning_content"] == "I thought about it"


def test_whitespace_only_thinking_is_omitted() -> None:
    session = _session(thinking_text="   \n ")
    message = build_response(session, "answer")["choices"][0]["message"]
    assert "reasoning_content" not in message


# ── truncation markers ───────────────────────────────────────────────────────

def test_recovered_overthinking_is_marked_in_the_reasoning() -> None:
    session = _session(thinking_text="long reasoning")
    response = build_response(session, "an answer", thinking_exhausted=True)
    status = response["de2generator"]["truncation_status"]
    message = response["choices"][0]["message"]
    assert status == "thinking_budget_exhausted_but_recovered"
    assert "[AUTOMATIC-RETRY-SUCCESSFUL-AFTER-THINKING-STALL]" in message["reasoning_content"]


def test_unrecovered_overthinking_is_marked_as_truncated() -> None:
    session = _session(thinking_text="long reasoning")
    response = build_response(session, "   ", thinking_exhausted=True)
    message = response["choices"][0]["message"]
    assert response["de2generator"]["truncation_status"] == "thinking_budget_exhausted"
    assert "[TRUNCATED-BECAUSE-OVERTHINKING-BUDGET]" in message["reasoning_content"]


def test_without_thinking_no_marker_appears() -> None:
    session = _session(thinking_text="")
    response = build_response(session, "answer", thinking_exhausted=True)
    assert response["de2generator"]["truncation_status"] == "no_truncation"


# ── translation merging ──────────────────────────────────────────────────────

def test_merge_removes_duplicated_boundary_characters() -> None:
    merged = merge_overlapping_translations(["<b> Bonjour</b>", "/b> Au revoir"])
    assert merged == "<b> Bonjour</b> Au revoir"


def test_merge_without_overlap_is_a_plain_concatenation() -> None:
    assert merge_overlapping_translations(["abc", "def"]) == "abcdef"


def test_merge_handles_edge_cases() -> None:
    assert merge_overlapping_translations([]) == ""
    assert merge_overlapping_translations(["only"]) == "only"


def test_merged_translation_skips_thinking_checkpoints() -> None:
    checkpoints = [_thinking_checkpoint("why"), _content_checkpoint("one "), _content_checkpoint("two")]
    translated = {1: "uno ", 2: "due"}
    assert merged_translation(checkpoints, translated) == "uno due"
    assert content_checkpoint_indices(checkpoints) == [1, 2]


def test_incomplete_translation_falls_back_to_the_original() -> None:
    checkpoints = [_content_checkpoint("one "), _content_checkpoint("two")]
    assert merged_translation(checkpoints, {1: "due"}) is None
    assert merged_translation(checkpoints, {}) is None


def test_build_response_prefers_a_complete_translation() -> None:
    session = _session(
        checkpoints=[_content_checkpoint("The harbour was calm.")],
        translated_checkpoints={0: "Il porto era calmo."},
        translation_language="IT",
    )
    response = build_response(session, "The harbour was calm.")
    assert response["choices"][0]["message"]["content"] == "Il porto era calmo."
    assert response["de2generator"]["translation_applied"] is True
    assert response["de2generator"]["translation_language"] == "IT"


def test_build_response_keeps_the_original_when_translation_is_partial() -> None:
    session = _session(
        checkpoints=[_content_checkpoint("one "), _content_checkpoint("two")],
        translated_checkpoints={0: "uno "},                      # checkpoint 1 missing
        translation_language="IT",
    )
    response = build_response(session, "one two")
    assert response["choices"][0]["message"]["content"] == "one two"
    assert response["de2generator"]["translation_applied"] is False
    assert session.translated_checkpoints, "left untouched for the caller"


# ── logprob clipping ────────────────────────────────────────────────────────

def test_clip_logprobs_covers_at_most_the_char_budget() -> None:
    entries = [{"t": "alpha"}, {"t": "beta"}, {"t": "gamma"}]
    assert clip_logprobs(entries, 14) == entries          # cumulative 5+4+5
    assert clip_logprobs(entries, 9) == entries[:2]       # 5+4 fits, +5 would not
    assert clip_logprobs(entries, 4) == []                # first token already exceeds
    assert clip_logprobs([], 10) == []
