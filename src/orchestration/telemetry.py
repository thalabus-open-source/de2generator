"""Supervision telemetry.

Every detector verdict that changes the fate of a checkpoint is recorded here so
it can later be aggregated (the TODO is a durable sink, e.g. Elasticsearch).
"""

import json

from core.models import GenerationSession
from utils import get_logger

log = get_logger()


def log_telemetry(session: GenerationSession, token_position: int,
                  detector: str, score: float, action: str) -> None:
    """Log a supervision event.  TODO: persist to Elasticsearch."""
    record = {
        "model":          session.model,
        "request_id":     session.request_id,
        "token_position": token_position,
        "detector":       detector,
        "score":          round(score, 4),
        "action":         action,
    }
    log.warning(f"[TELEMETRY] {json.dumps(record)}")
