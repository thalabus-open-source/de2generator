"""Scripted OpenAI-compatible LLM backend for exercising de²generator.

It exists so the supervisor can be driven through a *real* HTTP/SSE round trip
with deterministic, reproducible model behaviour — no GPU, no flakiness.

The scenario is selected by the ``model`` field, which de²generator forwards
unchanged, e.g. ``{"model": "mock/healthy"}``.  Every scenario is stateless:
whether this is a first attempt or a supervised retry is derived from the
messages themselves ("Continue from the following text" for a continuation
retry, ``/no_think`` for a no-think retry), so the same endpoint serves every
test without bookkeeping.

Scenarios
    mock/healthy      plain streaming answer, ends with a usage chunk
    mock/json         JSON answer closing ``}`` at each checkpoint boundary
    mock/degenerate   repetitive gibberish, then a healthy answer on continuation
    mock/overthink    long reasoning and no answer, then an answer without thinking
    mock/thinkloop    the same reasoning paragraph forever
    mock/empty        finishes without producing any text
    mock/error500     upstream failure, to check the proxy's 5xx mapping
    mock/nologprobs   refuses ``logprobs`` (400) and works when they are dropped
    mock/slow         trickles tokens, for cancellation tests

Usage
    python src/test/mock_llm_backend.py --port 8398
    GET /mock/requests   recorded upstream payloads (assert on proxy transparency)
"""

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Optional

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse

DEFAULT_PORT = int(os.getenv("MOCK_LLM_PORT", "8398"))
DEFAULT_HOST = os.getenv("MOCK_LLM_HOST", "127.0.0.1")

HEALTHY_ANSWER = (
    "The lighthouse keeper logged every passing ship.\n\n"
    "Each night he climbed the ninety steps with a tin cup of coffee, "
    "and each dawn he wrote the same summary: wind, visibility, traffic.\n\n"
    "That routine was the reason no wreck happened on his watch."
)
JSON_ANSWER = (
    '{"ship": "Aurora", "arrivals": ['
    '{"name": "Aurora", "berth": 3}, {"name": "Kestrel", "berth": 7}'
    ']}'
)
# Long enough (and varied enough) to pass every detector on its own: this is the
# checkpoint the supervisor must roll back *to*, and a continuation is only worth
# issuing if there is a healthy checkpoint to continue from.
HEALTHY_PREFIX = (
    "The lighthouse keeper logged every passing ship, and he kept the ledger in a "
    "drawer by the bed. Each night he climbed the ninety steps with a tin cup of "
    "coffee, and each dawn he wrote the same summary: wind, visibility, traffic. "
    "That routine was the reason no wreck happened on his watch, and the harbour "
    "master trusted it without asking."
)


_OVERTHINK_BLOCK = (
    "But then the reasoning continues, because the keeper must weigh the tide tables, "
    "the fuel reserve, the relay schedule, and the visibility forecast again.\n\n"
)
THOUGHT = (
    "The keeper checked the tide tables and the fuel reserve. "
    "The relay schedule was unchanged and visibility held above six miles."
)
TRANSLATED_ANSWER = "Il faro era acceso e il porto era al sicuro."
# What a well-behaved model adds after being told to continue: new prose, never a
# restatement of the text it was given — repeating it would look like degeneration.
CONTINUATION_ANSWER = (
    "He logged the last trawler of the dusk, then went below to sleep, "
    "and the harbour stayed upright behind him."
)
_SLOW_PIECES = ("The keeper ", "counted ", "the ships ", "one by one. ")

app = FastAPI(title="mock LLM backend", version="1.0.0")
_recorded: list[dict] = []
_served = 0                          # monotonic, survives the rolling window below
RECORD_WINDOW = 25


def _record(payload: dict, path: str = "/v1/chat/completions") -> None:
    global _served
    _served += 1
    _recorded.append({"at": time.time(), "path": path, "payload": payload})
    del _recorded[:-RECORD_WINDOW]


def _last_user_text(payload: dict) -> str:
    for message in reversed(payload.get("messages") or []):
        if isinstance(message.get("content"), str):
            return message["content"]
    return ""


def _all_text(payload: dict) -> str:
    parts = []
    for message in payload.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def _is_continuation(payload: dict) -> bool:
    return "Continue from the following text" in _last_user_text(payload)


# One paragraph of loop noise: ~500 chars, entropy ~2.6, one health check each.
DEGENERATE_PARAGRAPH = "aaaa bbbb cccc dddd eeee " * 20


def _degenerate_text(paragraphs: int = 4) -> str:
    """Prose that collapses into a loop a few hundred characters in.

    The opening has to be long enough to become a checkpoint of its own — a
    payload that is noise from the first byte leaves the rollback with nothing to
    keep — and the noise needs enough paragraphs to accumulate three strikes, which
    is what makes the supervisor abort mid-stream instead of at the end.
    """
    return HEALTHY_PREFIX + ("\n\n" + DEGENERATE_PARAGRAPH) * paragraphs


def _is_nothink_retry(payload: dict) -> bool:
    return "/no_think" in _all_text(payload)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


def _chunk(content: Optional[str] = None, reasoning: Optional[str] = None,
           finish: Optional[str] = None, logprobs: Optional[dict] = None) -> str:
    delta: dict = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning"] = reasoning
    choice: dict = {"index": 0, "delta": delta, "finish_reason": finish}
    if logprobs is not None:
        choice["logprobs"] = logprobs
    return _sse({"id": "mock", "object": "chat.completion.chunk", "choices": [choice]})


def _usage(prompt_tokens: int, completion_tokens: int,
           reasoning_tokens: int = 0) -> str:
    return _sse({
        "id": "mock",
        "object": "chat.completion.chunk",
        "choices": [],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "reasoning_tokens": reasoning_tokens,
        },
    })


def _done() -> str:
    return "data: [DONE]\n\n"


def _simple_logprobs(token: str) -> dict:
    """A two-candidate logprob bundle in the shape the gateway returns."""
    return {"content": [{
        "token": token,
        "logprob": -0.25,
        "top_logprobs": [
            {"token": token, "logprob": -0.25},
            {"token": "the", "logprob": -1.75},
        ],
    }]}


async def _stream_events(events: list[str], delay: float = 0.0):
    for event in events:
        if delay:
            await asyncio.sleep(delay)
        yield event


def _completion_json(events: list[str]) -> JSONResponse:
    """Collapse scripted chunks into one non-streaming completion."""
    content: list[str] = []
    reasoning: list[str] = []
    usage: dict = {}
    finish = "stop"
    for event in events:
        if not event.startswith("data: "):
            continue
        parsed = json.loads(event[len("data: "):])
        usage.update(parsed.get("usage") or {})
        for choice in parsed.get("choices") or []:
            delta = choice.get("delta") or {}
            content.append(delta.get("content") or "")
            reasoning.append(delta.get("reasoning") or "")
            finish = choice.get("finish_reason") or finish
    message: dict = {"role": "assistant", "content": "".join(content)}
    thinking = "".join(reasoning)
    if thinking:
        message["reasoning_content"] = thinking
    return JSONResponse({
        "id": "mock",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "mock",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage,
    })


async def _respond(events: list[str], payload: dict, delay: float = 0.0):
    """Serve the scripted events as SSE when stream=true, as JSON otherwise."""
    if payload.get("stream") is not True:
        return _completion_json(events)
    return StreamingResponse(_stream_events(events, delay=delay),
                             media_type="text/event-stream")


def _split_sentences(text: str) -> list[str]:
    """Split on blank lines, keeping the separators, so the parts rejoin exactly."""
    parts = text.split("\n\n")
    return [part + ("\n\n" if index < len(parts) - 1 else "")
            for index, part in enumerate(parts) if part]


def _answer_events(text: str, prompt_tokens: int, reasoning_tokens: int = 0,
                   logprobs: bool = False) -> list[str]:
    events = []
    completion_tokens = 0
    for piece in _split_sentences(text):
        completion_tokens += len(piece.split())
        first_token = (piece.split() or [piece[:1]])[0]
        events.append(
            _chunk(content=piece,
                   logprobs=_simple_logprobs(first_token) if logprobs else None)
        )
    events.append(_usage(prompt_tokens, max(completion_tokens, 1), reasoning_tokens))
    events.append(_chunk(finish="stop"))
    events.append(_done())
    return events


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    return await _completion(request, "/v1/chat/completions")


@app.post("/alt/v1/chat/completions")
async def chat_completions_alt(request: Request):
    """Second completions path, so a per-request endpoint override is observable."""
    return await _completion(request, "/alt/v1/chat/completions")


async def _completion(request: Request, path: str):
    payload = await request.json()
    _record(payload, path)
    scenario = str(payload.get("model", "")).split("/")[-1] or "healthy"
    prompt_tokens = len(_all_text(payload).split())
    want_logprobs = payload.get("logprobs") is True

    if scenario == "error500":
        return JSONResponse(status_code=500, content={"error": {"message": "backend exploded"}})

    if scenario == "nologprobs" and want_logprobs:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": "400: this deployment does not support logprobs"}},
        )

    # A recovery prompt asks the model to behave: keep emitting the original defect
    # here and the supervisor could never finish recovering, so the test would
    # measure the mock instead of the proxy.
    if _is_nothink_retry(payload):
        scenario = "healthy"          # told to stop thinking, it just answers
    elif _is_continuation(payload):
        scenario = "continuation"     # told to continue, it adds new prose
    elif scenario == "nologprobs" and not want_logprobs:
        scenario = "healthy"

    # Translations arrive non-streaming, as the translation client posts them
    if _last_user_text(payload).startswith("Translate the following text"):
        return _completion_json(_answer_events(TRANSLATED_ANSWER, prompt_tokens))

    if scenario in ("healthy", "continuation"):
        answer = CONTINUATION_ANSWER if scenario == "continuation" else HEALTHY_ANSWER
        events = _answer_events(answer, prompt_tokens, logprobs=want_logprobs)
    elif scenario == "json":
        events = _answer_events(JSON_ANSWER, prompt_tokens)
    elif scenario == "degenerate":
        events = _answer_events(_degenerate_text(), prompt_tokens)

    elif scenario == "overthink":
        events = [_chunk(reasoning=THOUGHT + "\n\n" + _OVERTHINK_BLOCK * 6)]
        events += [_usage(prompt_tokens, 900, 900), _chunk(finish="length"), _done()]
    elif scenario == "thinkloop":
        events = [_chunk(reasoning=_OVERTHINK_BLOCK) for _ in range(6)]
        events += [_usage(prompt_tokens, 800, 800), _chunk(finish="length"), _done()]
    elif scenario == "slow":
        events = [_chunk(content=piece) for piece in _SLOW_PIECES * 8]
        events += [_usage(prompt_tokens, 40), _chunk(finish="stop"), _done()]
        return await _respond(events, payload, delay=0.25)
    else:
        # mock/empty and anything unknown: a clean finish with no text at all
        events = [_usage(prompt_tokens, 0), _chunk(finish="stop"), _done()]

    return await _respond(events, payload)


@app.get("/v1/models")
async def models() -> JSONResponse:
    return JSONResponse({
        "object": "list",
        "data": [
            {"id": f"mock/{name}", "object": "model"}
            for name in ("healthy", "degenerate", "overthink", "empty", "json",
                         "thinkloop", "slow", "error500", "nologprobs")
        ],
    })


@app.get("/mock/requests")
async def recorded_requests() -> JSONResponse:
    """The payloads this backend actually received (transparency assertions)."""
    return JSONResponse({"served": _served, "count": len(_recorded),
                         "requests": _recorded})


@app.get("/mock/health")
async def health() -> dict:
    return {"status": "ok", "component": "mock-llm-backend"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Mock LLM backend for de²generator tests")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--host", type=str, default=DEFAULT_HOST)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
