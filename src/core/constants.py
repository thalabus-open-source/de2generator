"""Configuration constants for de²generator."""

import os
from .config import get_config_value


# Server configuration
LISTEN_HOST = os.getenv("DE2_HOST", get_config_value("server.host", default="0.0.0.0"))  # type: ignore
LISTEN_PORT = int(os.getenv("DE2_PORT", get_config_value("server.port", default=8356)))  # type: ignore
SERVER_PROTOCOL = str(get_config_value("server.protocol", "DE2_PROTOCOL", default="http"))  # type: ignore
SERVER_SSL_CERT_PATH = str(get_config_value("server.ssl_cert_path", "DE2_SSL_CERT", default=""))  # type: ignore
SERVER_SSL_KEY_PATH = str(get_config_value("server.ssl_key_path", "DE2_SSL_KEY", default=""))  # type: ignore

# Repetition detector
REPETITION_WINDOW_TOKENS: int = int(get_config_value("detection.repetition.window_tokens", default=100))  # type: ignore
REPETITION_CRITICAL_SCORE: int = int(get_config_value("detection.repetition.critical_score", default=40))  # type: ignore
REPETITION_WARNING_SCORE: int = int(get_config_value("detection.repetition.warning_score", default=70))  # type: ignore

# Plain-text checkpoint fallback
PLAIN_TEXT_CHECKPOINT_INTERVAL: int = int(get_config_value("recovery.plain_text_checkpoint_interval", default=250))  # type: ignore

# Entropy detector
ENTROPY_MIN_HEALTHY: float = float(get_config_value("detection.entropy.min_healthy", default=3.5))  # type: ignore
ENTROPY_MAX_HEALTHY: float = float(get_config_value("detection.entropy.max_healthy", default=7.0))  # type: ignore
ENTROPY_MIN_CHARS: int = int(get_config_value("detection.entropy.min_chars", default=100))  # type: ignore

# Thinking-phase loop detector (identical thinking blocks repeated verbatim)
THINKING_LOOP_MIN_CHARS: int = int(get_config_value("detection.thinking_loop.min_chars", default=40))  # type: ignore
THINKING_LOOP_MAX_REPEATS: int = int(get_config_value("detection.thinking_loop.max_repeats", default=3))  # type: ignore

# Semantic Progress Detector
SEMANTIC_LLM_ENABLED: bool = bool(get_config_value("semantic_llm.enabled", "SEMANTIC_LLM_ENABLED", default=True))  # type: ignore
SEMANTIC_VERIFIER_ENDPOINT: str = str(get_config_value("semantic_llm.endpoint", "SEMANTIC_VERIFIER_ENDPOINT", default="http://localhost:8350/v1/chat/completions"))  # type: ignore
SEMANTIC_VERIFIER_MODEL: str = str(get_config_value("semantic_llm.model", "SEMANTIC_VERIFIER_MODEL", default="surogate/Qwen3.5-2B-FP8"))  # type: ignore
SEMANTIC_VERIFIER_TIMEOUT: float = float(get_config_value("semantic_llm.timeout_seconds", "SEMANTIC_VERIFIER_TIMEOUT", default=5.0))  # type: ignore
SEMANTIC_MIN_BLOCK_CHARS: int = int(get_config_value("semantic_llm.min_block_chars", default=100))  # type: ignore
SEMANTIC_MAX_MODEL_LEN: int = int(get_config_value("semantic_llm.max_model_len", "SEMANTIC_MAX_MODEL_LEN", default=0))  # type: ignore  # 0 = no truncation
SEMANTIC_MAX_ANSWER_TOKENS: int = int(get_config_value("semantic_llm.max_answer_tokens", default=200))  # type: ignore
SEMANTIC_SAFETY_BUFFER_TOKENS: int = int(get_config_value("semantic_llm.safety_buffer_tokens", default=64))  # type: ignore
SEMANTIC_MIN_FAILING_CATEGORIES: int = int(get_config_value("semantic_llm.min_failing_categories", default=2))  # type: ignore

# Thinking token estimation
THINKING_CHARS_PER_TOKEN: int = int(get_config_value("thinking.chars_per_token", default=4))  # type: ignore
THINKING_BUDGET_EXHAUSTION_THRESHOLD: float = float(get_config_value("thinking.budget_exhaustion_threshold", default=1.1))  # type: ignore

# Semantic LLM availability cooldown
SEMANTIC_LLM_COOLDOWN_DURATION: float = float(get_config_value("thinking.cooldown_duration_seconds", default=300))  # type: ignore

# Detection strategy:
# - Structure validators (JSON/XML/HTML syntax): INSTANT REJECTION on syntax error (no strikes)
# - Quality detectors (Repetition, Entropy, Semantic): THREE-STRIKE RULE to prevent false positives
#   * Strike 1: severity="warning" (candidate_reject)
#   * Strike 2: severity="warning" (candidate_reject)
#   * Strike 3: severity="critical" (confirmed rejection, triggers rollback)

# Maximum supervised recovery loops per request
MAX_RECOVERY_ATTEMPTS: int = int(get_config_value("recovery.max_attempts", default=3))  # type: ignore
MAX_FULL_RESTARTS: int = int(get_config_value("recovery.max_full_restarts", default=2))  # type: ignore

# Maximum number of LLM calls to keep in the UI history panel
UI_MAX_HISTORY: int = int(get_config_value("ui.max_history", default=100))  # type: ignore
UI_ENABLED: bool = bool(get_config_value("ui.enabled", "DE2_UI_ENABLED", default=True))  # type: ignore
UI_SHOW_CONTENT: bool = bool(get_config_value("ui.show_content", "DE2_UI_SHOW_CONTENT", default=True))  # type: ignore

# Translation service
TRANSLATION_ENABLED: bool = bool(get_config_value("translation.enabled", "TRANSLATION_ENABLED", default=False))  # type: ignore
TRANSLATION_ENDPOINT: str = str(get_config_value("translation.endpoint", "TRANSLATION_ENDPOINT", default=""))  # type: ignore
TRANSLATION_MODEL: str = str(get_config_value("translation.model", "TRANSLATION_MODEL", default="tencent/Hy-MT2-7B-FP8"))  # type: ignore
TRANSLATION_TIMEOUT: float = float(get_config_value("translation.timeout_seconds", "TRANSLATION_TIMEOUT", default=15.0))  # type: ignore
TRANSLATION_DEBUG_FORCE_LANGUAGE: str = str(get_config_value("translation.debug_force_language", "TRANSLATION_FORCE_LANG", default="") or "")  # type: ignore

# vLLM backend URL
VLLM_BASE_URL: str = str(get_config_value("vllm.base_url", "VLLM_BASE_URL", default="https://llm.thalabus.ai"))  # type: ignore

# Abort the backend call if no first token arrives within this time (queue + prompt prefill)
VLLM_PREFILL_TIMEOUT: float = float(get_config_value("vllm.prefill_timeout_seconds", default=600))  # type: ignore
# Abort the backend call if the stream stays silent this long after the first token
VLLM_STALL_TIMEOUT: float = float(get_config_value("vllm.stream_stall_timeout_seconds", default=300))  # type: ignore
