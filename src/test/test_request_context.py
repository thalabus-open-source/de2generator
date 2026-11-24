"""Request parsing: endpoint overrides, extension block, translation precedence.

These are the rules the supervisor depends on before a single token is streamed.
Run: pytest src/test/test_request_context.py
"""

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1]
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from orchestration.request_context import parse_request, resolve_translation_language  # noqa: E402


def _getter(mapping: dict):
    """Stand-in for core.config.get_config_value backed by a dict."""
    def get_value(key_path, env_var=None, default=None):
        return mapping.get(key_path, default)
    return get_value


OFF = {"translation.enabled": False}


def _translation_config(**overrides) -> dict:
    config = {
        "translation.enabled": True,
        "translation.debug_force_language": "",
        "translation.endpoint": "http://translator/v1/chat/completions",
    }
    config.update(overrides)
    return config


# ── backend endpoint ─────────────────────────────────────────────────────────

def test_extension_block_is_consumed_and_never_forwarded() -> None:
    body = {
        "model": "m",
        "messages": [{"role": "user", "content": "hi"}],
        "de2generator": {"agent_name": "AgentAssistant", "goal_id": "g1"},
    }
    context = parse_request(body, get_value=_getter(OFF))
    assert "de2generator" not in context.request_data
    assert body["de2generator"]["agent_name"] == "AgentAssistant"      # caller untouched
    assert context.agent_name == "AgentAssistant"
    assert context.goal_id == "g1"


def test_model_endpoint_overrides_the_default_backend() -> None:
    body = {"model": "m", "de2generator": {"model_endpoint": "http://other:9000"}}
    context = parse_request(body, get_value=_getter(OFF))
    assert context.endpoint == "http://other:9000"


def test_model_endpoint_suffixes_are_normalized() -> None:
    for given, expected in (
        ("http://h:1/", "http://h:1"),
        ("http://h:1/v1", "http://h:1"),
        ("http://h:1/v1/chat/completions", "http://h:1"),
        ("http://h:1/v1/chat/completions/", "http://h:1"),
        ("http://h:1/v1beta/chat", "http://h:1/v1beta/chat"),
    ):
        context = parse_request(
            {"model": "m", "de2generator": {"model_endpoint": given}},
            get_value=_getter(OFF),
        )
        assert context.endpoint == expected, given


def test_without_override_the_configured_backend_is_used() -> None:
    context = parse_request({"model": "m"}, get_value=_getter(OFF))
    from core.endpoints import get_vllm_base_url
    assert context.endpoint == get_vllm_base_url()


# ── thinking + sampling parameters ───────────────────────────────────────────

def test_thinking_defaults_match_openai_compatibility() -> None:
    context = parse_request({"model": "m"}, get_value=_getter(OFF))
    assert context.enable_thinking is True
    assert context.thinking_budget_tokens is None


def test_thinking_settings_are_read_from_chat_template_kwargs() -> None:
    context = parse_request(
        {"model": "m", "chat_template_kwargs": {"thinking_budget": 2048, "enable_thinking": False}},
        get_value=_getter(OFF),
    )
    assert context.thinking_budget_tokens == 2048
    assert context.enable_thinking is False


def test_reasoning_effort_precedence() -> None:
    top = parse_request({"reasoning_effort": "high",
                         "chat_template_kwargs": {"reasoning_effort": "low"},
                         "de2generator": {"reasoning_effort": "medium"}},
                        get_value=_getter(OFF))
    template = parse_request({"chat_template_kwargs": {"reasoning_effort": "low"},
                              "de2generator": {"reasoning_level": "medium"}},
                             get_value=_getter(OFF))
    extension = parse_request({"de2generator": {"reasoning_level": "medium"}},
                              get_value=_getter(OFF))
    assert top.reasoning_effort == "high"
    assert template.reasoning_effort == "low"
    assert extension.reasoning_effort == "medium"
    assert top.model_parameters["reasoning_effort"] == "high"


def test_model_parameters_collect_sampling_settings() -> None:
    context = parse_request(
        {"model": "qwen", "top_p": 0.9, "repetition_penalty": 1.05, "presence_penalty": 0},
        get_value=_getter(OFF),
    )
    assert context.model_parameters["model"] == "qwen"
    assert context.model_parameters["top_p"] == 0.9
    assert context.model_parameters["repetition_penalty"] == 1.05
    assert context.model_parameters["presence_penalty"] == 0


# ── translation resolution ───────────────────────────────────────────────────

def test_translation_disabled_yields_no_language() -> None:
    assert resolve_translation_language("AgentAssistant", {}, get_value=_getter(OFF)) is None


def test_debug_force_language_overrides_the_agent_filter() -> None:
    config = _translation_config(
        **{"translation.debug_force_language": "it"},
        **{"translation.thalabus.enable_only_for_agents": ["SomethingElse"]},
    )
    assert resolve_translation_language("Other", {}, get_value=_getter(config)) == "IT"


def test_agent_filter_blocks_unlisted_agents() -> None:
    config = _translation_config(**{"translation.thalabus.enable_only_for_agents": ["AgentAssistant"]})
    assert resolve_translation_language("OtherAgent", {"translate_to": "de"},
                                        get_value=_getter(config)) is None
    assert resolve_translation_language(None, {"translate_to": "de"},
                                        get_value=_getter(config)) is None


def test_agent_filter_allows_listed_agents() -> None:
    config = _translation_config(**{"translation.thalabus.enable_only_for_agents": ["AgentAssistant"]})
    assert resolve_translation_language("AgentAssistant", {"translate_to": "de"},
                                        get_value=_getter(config)) == "DE"


def test_ui_language_wins_over_request_language() -> None:
    config = _translation_config(**{"translation.thalabus.ui_language": "fr"})
    assert resolve_translation_language("A", {"translate_to": "de"},
                                        get_value=_getter(config)) == "FR"


def test_request_language_used_when_ui_language_absent() -> None:
    assert resolve_translation_language("A", {"translate_to": " es "},
                                        get_value=_getter(_translation_config())) == "ES"


def test_no_language_selected_means_no_translation() -> None:
    assert resolve_translation_language("A", {}, get_value=_getter(_translation_config())) is None


def test_context_carries_the_resolved_language() -> None:
    config = _translation_config(**{"translation.thalabus.ui_language": "pl"})
    context = parse_request(
        {"model": "m", "de2generator": {"agent_name": "A"}}, get_value=_getter(config)
    )
    assert context.translate_to == "PL"
    assert context.translation_enabled is True
