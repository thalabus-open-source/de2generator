"""Request orchestration for de²generator.

The three pieces the HTTP layer needs, kept free of FastAPI so they can be
imported and tested on their own:

* :mod:`.request_context` — parse a request body into a decision-ready object
* :mod:`.supervisor`      — the degeneration-supervision state machine
* :mod:`.cancellation`    — opt-in aborts via ``POST /v1/cancel``
"""

from .cancellation import (
    CancelResult,
    RequestControl,
    cancel_request,
    extract_cancellation_token,
    register_request,
    run_cancellable_generation,
    unregister_request,
    validate_cancellation_token,
)
from .request_context import RequestContext, parse_request, resolve_translation_language
from .supervisor import GenerationSupervisor, supervise_generation
from .telemetry import log_telemetry

__all__ = [
    "CancelResult",
    "RequestControl",
    "RequestContext",
    "GenerationSupervisor",
    "cancel_request",
    "extract_cancellation_token",
    "log_telemetry",
    "parse_request",
    "register_request",
    "resolve_translation_language",
    "run_cancellable_generation",
    "supervise_generation",
    "unregister_request",
    "validate_cancellation_token",
]
