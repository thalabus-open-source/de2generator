"""Data models for de²generator."""

import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DetectionResult:
    """Result from a degeneration detector."""
    score:    float   # 0..100 — 100 is fully healthy
    severity: str     # "ok" | "warning" | "critical"
                      # NOTE: Structure validators (JSON/XML/HTML syntax) should return
                      #       "critical" immediately on syntax error (no three-strike rule).
                      #       Quality detectors (repetition, entropy, semantic) should use
                      #       "warning" for strikes 1-2, "critical" for strike 3.
    reason:   str


@dataclass
class Checkpoint:
    """Checkpoint in generation stream for recovery."""
    token_position:    int
    generated_text:    str
    metrics_snapshot:  dict
    entropy:           Optional[float] = None  # Shannon entropy for this checkpoint
    entropy_status:    str = "ok"  # "ok", "candidate_reject", "rejected"


@dataclass
class GenerationSession:
    """State for a single generation request."""
    request_id:            str
    model:                 str
    messages:              list
    generated_text:        str  = ""
    token_count:           int  = 0
    thinking_tokens:       int  = 0  # Tokens consumed by thinking phase (from vLLM usage)
    checkpoints:           list = field(default_factory=list)
    recovery_attempts:     int  = 0
    thinking_text:         str  = ""
    original_request:      dict = field(default_factory=dict)  # Original request for logging
    thinking_was_requested: bool = False  # Whether thinking was requested via thinking_budget > 0
    should_log_interaction: bool = False  # Whether to log this interaction (thinking overflow occurred)
    log_file_path:         str  = ""  # Path to the log file if interaction was logged
    start_time:            float = field(default_factory=time.time)
    ttft_seconds:          Optional[float] = None
    thinking_phase_duration: float = 0.0
    input_tokens:          int  = 0
    reasoning_tokens:      int  = 0
    output_tokens:         int  = 0
    rejected_checkpoints_count: int = 0
    rejected_checkpoints_tokens: int = 0
    truncation_status:     str  = "no_truncation"
    translated_checkpoints: dict = field(default_factory=dict)  # cp_idx -> translated_text
    translation_language:   str  = ""  # target language code (e.g. "IT")
