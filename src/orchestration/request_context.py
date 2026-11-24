"""Parsing of an incoming chat-completion request.

Everything the supervisor needs to know *before* it starts streaming is derived
here, once, instead of being re-mined from the raw request body throughout the
supervision loop:

* which backend to call (per-request override, normalised — never forwarded)
* the ``de2generator`` extension fields the UI shows (agent, goal)
* whether and to which language the answer should be translated
* thinking configuration (budget, enable_thinking, reasoning effort)
* the sampling parameters shown on the UI transaction card

The translation resolution keeps the documented precedence:
``debug_force_language`` → agent allow-list → UI-selected language →
per-request ``translate_to`` → no translation.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Optional

from core.config import get_config_value
from core.endpoints import resolve_endpoint
from utils import get_logger

log = get_logger()


@dataclass(frozen=True)
class RequestContext:
    """Immutable view of one client request."""

    request_data: dict                       # client body, extension block removed
    extension: dict = field(default_factory=dict)   # consumed "de2generator" block
    endpoint: str = ""
    agent_name: Optional[str] = None
    goal_id: Optional[str] = None
    translate_to: Optional[str] = None
    thinking_budget_tokens: Optional[int] = None
    enable_thinking: bool = True
    reasoning_effort: Optional[str] = None
    model: str = "unknown"
    messages: list = field(default_factory=list)
    model_parameters: dict = field(default_factory=dict)

    @property
    def translation_enabled(self) -> bool:
        return bool(self.translate_to)


def _read_extension(body: Mapping) -> dict:
    """Pop the ``de2generator`` extension block (consumed here, never forwarded)."""
    extension = body.get("de2generator") if isinstance(body, Mapping) else None
    return extension if isinstance(extension, dict) else {}


def resolve_translation_language(
    agent_name: Optional[str],
    extension: Mapping,
    *,
    get_value: Callable[..., Any] = get_config_value,
) -> Optional[str]:
    """Decide the target language for this request, or ``None``.

    ``get_value`` is injectable so the precedence rules are testable without
    touching the live configuration.
    """
    enabled = get_value("translation.enabled", default=False)
    debug_lang = get_value("translation.debug_force_language", default="")
    endpoint = get_value("translation.endpoint", default="")
    log.debug(
        f"[Translation check] enabled={enabled}, debug_lang={debug_lang!r}, endpoint={endpoint}, "
        f"agent={agent_name!r}"
    )
    if not enabled:
        return None

    # 1. debug_force_language overrides everything, including the agent allow-list
    if isinstance(debug_lang, str) and debug_lang.strip():
        language = debug_lang.strip().upper()
        log.info(f"[Translation] Using debug_force_language = {language} (overrides agent filter)")
        return language

    # 2. agent allow-list
    agent_filter = get_value("translation.thalabus.enable_only_for_agents", default=None)
    if isinstance(agent_filter, list) and agent_filter:
        if not agent_name or agent_name not in agent_filter:
            log.info(
                f"[Translation] Disabled: agent {agent_name!r} not in enable_only_for_agents list {agent_filter}"
            )
            return None
        log.info(f"[Translation] Allowed for agent {agent_name!r}")
    else:
        log.info("[Translation] Filter not set, enabled for all agents")

    # 3. UI-selected language, then 4. per-request translate_to
    language: Optional[str] = None
    ui_lang = get_value("translation.thalabus.ui_language", default=None)
    if isinstance(ui_lang, str) and ui_lang.strip():
        language = ui_lang.strip().upper()
        log.info(f"[Translation] Using ui_language = {language}")
    else:
        raw_lang = extension.get("translate_to")
        if isinstance(raw_lang, str) and raw_lang.strip():
            language = raw_lang.strip().upper()
            log.info(f"[Translation] Using translate_to from request = {language}")

    if language:
        log.info(f"[Translation] enabled → target language: {language}")
    else:
        log.info("[Translation] allowed by filter, but no target language specified")
    return language


def _model_parameters(request_data: Mapping, reasoning_effort: Optional[str]) -> dict:
    """Sampling parameters displayed on the UI transaction card."""
    return {
        "model": request_data.get("model"),
        "top_p": request_data.get("top_p"),
        "top_k": request_data.get("top_k"),
        "frequency_penalty": request_data.get("frequency_penalty"),
        "presence_penalty": request_data.get("presence_penalty"),
        "no_repeat_ngram_size": request_data.get("no_repeat_ngram_size"),
        "repetition_penalty": request_data.get("repetition_penalty"),
        "reasoning_effort": reasoning_effort,
    }


def parse_request(
    body: Mapping,
    *,
    get_value: Callable[..., Any] = get_config_value,
) -> RequestContext:
    """Parse a raw request body into a :class:`RequestContext`.

    The returned ``request_data`` is a copy with the ``de2generator`` block
    removed, so a backend can never observe Thalabus-private fields.
    """
    extension = _read_extension(body)
    request_data = {k: v for k, v in body.items() if k != "de2generator"}

    endpoint = resolve_endpoint(extension.get("model_endpoint"))
    agent_name = extension.get("agent_name")
    goal_id = extension.get("goal_id")
    if extension.get("model_endpoint"):
        log.info(f"Using custom LLM endpoint from de2generator config: {endpoint}")

    translate_to = resolve_translation_language(agent_name, extension, get_value=get_value)

    chat_template_kwargs: dict = request_data.get("chat_template_kwargs") or {}
    thinking_budget_tokens: Optional[int] = chat_template_kwargs.get("thinking_budget")
    enable_thinking_flag: bool = chat_template_kwargs.get("enable_thinking", True)

    # Requested reasoning level: top-level (OpenAI-compat) wins, then template kwargs,
    # then the de2generator extension block.
    reasoning_effort = (
        request_data.get("reasoning_effort")
        or chat_template_kwargs.get("reasoning_effort")
        or extension.get("reasoning_effort")
        or extension.get("reasoning_level")
    )

    context = RequestContext(
        request_data=dict(request_data),
        extension=dict(extension),
        endpoint=endpoint,
        agent_name=agent_name,
        goal_id=goal_id,
        translate_to=translate_to,
        thinking_budget_tokens=thinking_budget_tokens,
        enable_thinking=enable_thinking_flag,
        reasoning_effort=reasoning_effort,
        model=request_data.get("model", "unknown"),
        messages=list(request_data.get("messages", [])),
        model_parameters=_model_parameters(request_data, reasoning_effort),
    )
    log.debug(
        f"[Request parsed] model={context.model} endpoint={context.endpoint} "
        f"enable_thinking={context.enable_thinking} "
        f"thinking_budget={context.thinking_budget_tokens} "
        f"reasoning_effort={context.reasoning_effort} translate_to={context.translate_to}"
    )
    return context
