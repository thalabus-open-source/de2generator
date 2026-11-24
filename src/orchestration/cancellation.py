"""Client-triggered cancellation of an in-flight generation.

A client that wants to be able to abort a generation sends a
``de2generator.cancellation_token`` with the completion request.  The token is
registered here together with a :class:`RequestControl`; ``POST /v1/cancel``
flips the control's event and the supervisor's streaming loop stops as soon as it
notice — the completion then answers ``499 client_cancelled``.

The registry is process-local and guarded by an asyncio lock, so the same token
can never be claimed by two concurrent generations.
"""

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Tuple

MAX_CANCELLATION_TOKEN_LEN = 256


@dataclass
class RequestControl:
    """Lifecycle handle of one cancellable generation."""

    request_id: str
    cancel_event: asyncio.Event
    state: str = "active"          # "active" | "cancelling" | "completed" | "cancelled"


@dataclass
class CancelResult:
    """Outcome of a cancel request."""

    control: Optional[RequestControl]
    newly_cancelled: bool

    @property
    def status(self) -> str:
        if self.control is None:
            return "not_found"
        return "cancelled" if self.newly_cancelled else "already_cancelled"


_registry: dict[str, RequestControl] = {}
_registry_lock = asyncio.Lock()


def validate_cancellation_token(value: object) -> str:
    """Normalise a cancellation token, raising ``ValueError`` when unusable."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("cancellation_token must be a non-empty string")
    token = value.strip()
    if len(token) > MAX_CANCELLATION_TOKEN_LEN:
        raise ValueError(
            f"cancellation_token must not exceed {MAX_CANCELLATION_TOKEN_LEN} characters"
        )
    return token


def extract_cancellation_token(body: Mapping) -> Optional[str]:
    """Read ``de2generator.cancellation_token`` from a completion request body.

    Returns ``None`` when the request is not cancellable, the token is missing or
    the extension block has the wrong shape — mirroring the lenient behaviour
    clients see today.  A malformed (non-empty but invalid) token raises.
    """
    de2generator_config = body.get("de2generator") if isinstance(body, Mapping) else None
    if not isinstance(de2generator_config, Mapping):
        return None
    token = de2generator_config.get("cancellation_token")
    if token is None:
        return None
    return validate_cancellation_token(token)


async def register_request(cancellation_token: str, request_id: str) -> Optional[RequestControl]:
    """Claim ``cancellation_token`` for ``request_id``.

    Returns the new control, or ``None`` when the token is already in use.
    """
    async with _registry_lock:
        if cancellation_token in _registry:
            return None
        control = RequestControl(request_id=request_id, cancel_event=asyncio.Event())
        _registry[cancellation_token] = control
        return control


async def cancel_request(cancellation_token: str) -> CancelResult:
    """Signal cancellation.  Idempotent: repeated calls report ``already_cancelled``."""
    async with _registry_lock:
        control = _registry.get(cancellation_token)
        newly_cancelled = control is not None and control.state == "active"
        if newly_cancelled and control is not None:
            control.state = "cancelling"
            control.cancel_event.set()
        return CancelResult(control=control, newly_cancelled=newly_cancelled)


async def unregister_request(cancellation_token: str, control: RequestControl) -> None:
    """Release the token, but only if it still belongs to ``control``."""
    async with _registry_lock:
        if _registry.get(cancellation_token) is control:
            del _registry[cancellation_token]


async def snapshot() -> dict[str, str]:
    """``token -> state`` view of the registry (for tests and debugging)."""
    async with _registry_lock:
        return {token: control.state for token, control in _registry.items()}


async def _clear() -> None:
    """Empty the registry (test helper)."""
    async with _registry_lock:
        _registry.clear()


async def run_cancellable_generation(
    generate: Callable[..., Awaitable[Tuple[dict, object]]],
    body: dict,
    request: object,
    request_id: str,
    control: RequestControl,
) -> Optional[Tuple[dict, object]]:
    """Run ``generate`` unless the control is cancelled first.

    Returns the ``(response, session)`` pair, or ``None`` when cancellation won
    the race — the caller turns that into a ``499``.  Whichever task loses is
    cancelled and awaited, so no background generation survives the response.
    """
    generation_task = asyncio.create_task(
        generate(body, request=request, request_id=request_id)
    )
    cancellation_task = asyncio.create_task(control.cancel_event.wait())
    try:
        done, _ = await asyncio.wait(
            {generation_task, cancellation_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if generation_task in done:
            control.state = "completed"
            return await generation_task
        if cancellation_task in done:
            control.state = "cancelled"
            generation_task.cancel()
            await asyncio.gather(generation_task, return_exceptions=True)
            return None
        raise RuntimeError("Generation race completed without a finished task")
    finally:
        cancellation_task.cancel()
        if not generation_task.done():
            generation_task.cancel()
        await asyncio.gather(generation_task, cancellation_task, return_exceptions=True)
