# Configuration Documentation

## Overview

de²generator reads configuration from `config.json` at startup. Precedence is
**environment variable → `config.json` → built-in default**, and
[`src/core/constants.py`](../src/core/constants.py) is the authoritative list of every key,
env name and default — several keys have **no** env override at all. Verified against source
on 2026-11-14.

## Configuration File Format

```json
{
  "model_endpoint": "http://192.168.30.110:8000/v1/chat/completions",
  "semantic_llm": {
    "enabled": true,
    "endpoint": "http://192.168.30.31:8350/v1/chat/completions",
    "model": "surogate/Qwen3.5-2B-FP8",
    "timeout_seconds": 5.0
  },
  "detection": {
    "entropy": {
      "min_healthy": 3.5,
      "max_healthy": 7.0,
      "min_block_chars": 100
    },
    "repetition": {
      "window_tokens": 100,
      "critical_score": 40,
      "warning_score": 60
    }
  },
  "recovery": {
    "max_attempts": 3,
    "max_full_restarts": 1,
    "checkpoint_interval_tokens": 250
  },
  "ui": {
    "enabled": true,
    "max_history": 50,
    "show_content": true,
    "thinking_chars_per_token": 4
  },
  "server": {
    "protocol": "http",
    "host": "0.0.0.0",
    "port": 8356,
    "ssl_cert_path": "",
    "ssl_key_path": ""
  }
}
```

## Configuration Reference

### Root Level

There are no scalar root keys. The root object holds sections: `vllm`, `semantic_llm`,
`detection`, `recovery`, `thinking`, `translation`, `ui`, `server`.

#### `vllm` Section

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `base_url` | string | `https://llm.thalabus.ai` | Upstream backend base URL; `/v1/chat/completions` and `/v1/models` are appended, so do **not** include a path (env: `VLLM_BASE_URL`) |
| `prefill_timeout_seconds` | float | `600` | Abort if no first token arrives within this window (queue + prefill) |
| `stream_stall_timeout_seconds` | float | `300` | Abort if the stream goes silent this long after the first token |

### `semantic_llm` Section

Configures the optional semantic quality verifier LLM.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable semantic verification (env: `SEMANTIC_LLM_ENABLED`) |
| `endpoint` | string | `http://192.168.30.31:8350/v1/chat/completions` | Semantic verifier endpoint (env: `SEMANTIC_VERIFIER_ENDPOINT`) |
| `model` | string | `surogate/Qwen3.5-2B-FP8` | Model name on verifier (env: `SEMANTIC_VERIFIER_MODEL`) |
| `timeout_seconds` | float | `5.0` | Request timeout (env: `SEMANTIC_VERIFIER_TIMEOUT`) |
| `max_model_len` | int | `0` | Verifier context window in tokens; `0` = never truncate (env: `SEMANTIC_MAX_MODEL_LEN`) |
| `max_answer_tokens` | int | `200` | Tokens reserved for the verifier's answer (also sent as `max_tokens`) |
| `safety_buffer_tokens` | int | `64` | Extra margin; prompt is cut to `max_model_len - max_answer_tokens - safety_buffer_tokens` |
| `cooldown_minutes` | — | — | **Not a key here** — the cooldown is top-level `thinking.cooldown_duration_seconds` (default `300`, seconds, no env override) |
| `min_block_chars` | int | `200` | Minimum chars to evaluate (no env override) |
| `min_failing_categories` | int | `2` | Categories needed to reject (no env override) |

**Notes:**
- If semantic verifier is unavailable, entropy detection continues
- Failed verifier requests increment a failure counter; after 3 consecutive failures, a 5-minute cooldown begins
- During cooldown, all checkpoints bypass semantic validation
- Set `enabled: false` to completely disable (saves ~50-100ms per checkpoint)

### `detection` Section

#### `entropy` Subsection

Configures the Shannon entropy-based degeneration detector.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `min_healthy` | float | `3.5` | Lower bound for healthy entropy (bits/byte) |
| `max_healthy` | float | `7.0` | Upper bound for healthy entropy (bits/byte) |
| `min_chars` | int | `100` | Minimum block size for calculation (requires statistical significance) |

**Healthy Range:**
- English, Italian, German, French, Spanish: 6.0-7.0 bits/byte
- Technical/mixed text: 6.5-7.5 bits/byte
- Below 3.5: degenerate (character repetition, single words)
- Above 7.5: possible gibberish or binary data

**Tuning:**
- If too many false positives (low entropy legitimate text): decrease `min_healthy` (e.g., 3.2)
- If too many false negatives (high repetition not detected): increase `max_healthy` (e.g., 7.5)
- Adjust `min_chars` for faster detection: lower = faster but less accurate; higher = more statistical confidence
- There is no per-detector `enabled` switch: entropy, repetition and thinking-loop detection always run

#### `repetition` Subsection

Configures token-level repetition analysis.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `window_tokens` | int | `100` | Rolling window size (tokens) |
| `critical_score` | int | `40` | Health score threshold for critical (0-100) |
| `warning_score` | int | `70` | Health score threshold for warning (0-100) |

**Scoring:**
- Health score = `distinct_ratio × 40% + (1 - bigram_ratio) × 35% + (1 - trigram_ratio) × 25%`
- Below `critical_score`: immediate rejection
- Between `critical_score` and `warning_score`: warning (three-strike rule applies)
- Above `warning_score`: OK

**Tuning:**
- If detecting repeated prompts as errors: increase `critical_score`
- If not detecting repetition patterns: decrease `critical_score`

#### `thinking_loop` Subsection

Flags an identical reasoning block repeating verbatim (the classic "stuck thinking" failure).

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `min_chars` | int | `40` | Ignore blocks shorter than this when comparing |
| `max_repeats` | int | `3` | Consecutive identical blocks before flagging |

### `recovery` Section

Controls checkpoint strategy and recovery behavior.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_attempts` | int | `3` | Maximum generation retries per session |
| `max_full_restarts` | int | `2` | Maximum full generation restarts (not checkpoints) |
| `plain_text_checkpoint_interval` | int | `250` | Checkpoint every N tokens for plain-text answers |

**Behavior:**
- After 3 consecutive `REJECTED` checkpoints, fall back to best attempt
- If `max_full_restarts` reached, return current best output
- If recovery succeeds, add "recovery_checkpoint" to metadata

### `ui` Section

Frontend UI configuration.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `true` | Enable web UI (env: `DE2_UI_ENABLED`) |
| `max_history` | int | `100` | Number of past generations to display (FIFO retention) |
| `show_content` | bool | `true` | Show actual content in UI (env: `DE2_UI_SHOW_CONTENT`) |
| `thinking_chars_per_token` | — | — | **Not a key here** — the ratio is top-level `thinking.chars_per_token`, see below |

**Notes:**
- When `enabled: false`, UI displays "UI IS DISABLED BY CONFIGURATION" banner and disables all interactive features
- When `show_content: false`, UI still displays cards, metrics, and structure but replaces all content/thinking text with "[...] OMITTED [...]"
- `max_history` limits retention to latest N messages using FIFO (First-In-First-Out) eviction
- Older generations beyond `max_history` are removed from UI memory (but still in logs)
- `thinking.chars_per_token` is the heuristic used for UI progress bars; actual may vary

**Use Cases:**
- `enabled: false` — Disable UI for production deployments where monitoring is done via logs/telemetry
- `show_content: false` — Enable UI for monitoring without exposing sensitive content (compliance, security)

### `server` Section

Server network and protocol configuration.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `protocol` | string | `"http"` | Server protocol: `"http"` or `"https"` (env: `DE2_PROTOCOL`) |
| `host` | string | `"0.0.0.0"` | Bind address (env: `DE2_HOST`) |
| `port` | int | `8356` | Server port (env: `DE2_PORT`) |
| `ssl_cert_path` | string | `""` | Path to SSL certificate file (required for HTTPS) (env: `DE2_SSL_CERT`) |
| `ssl_key_path` | string | `""` | Path to SSL private key file (required for HTTPS) (env: `DE2_SSL_KEY`) |

**Notes:**
- Protocol defaults to HTTP for simplicity; HTTPS requires valid certificate and key files
- When `protocol: "https"`, both `ssl_cert_path` and `ssl_key_path` must be set to existing files
- Server validates certificate/key paths at startup and exits with error if missing
- Use `host: "127.0.0.1"` to bind only to localhost (restrict external access)
- Use `host: "0.0.0.0"` to bind to all network interfaces

**HTTPS Example:**
```json
{
  "server": {
    "protocol": "https",
    "host": "0.0.0.0",
    "port": 8356,
    "ssl_cert_path": "/etc/ssl/certs/de2generator.crt",
    "ssl_key_path": "/etc/ssl/private/de2generator.key"
  }
}
```

### `thinking` Section

Reasoning-model budget handling and the only home of the cooldown/ratio knobs.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `chars_per_token` | int | `4` | Char→token estimate for thinking text |
| `budget_exhaustion_threshold` | float | `1.1` | Ratio above which the thinking budget counts as exhausted (empty answer + long reasoning) |
| `cooldown_duration_seconds` | float | `300` | How long to stop calling a failing semantic verifier |

### `translation` Section

Optionally translates the final answer for display; disabled by default and in the deployed
`config.json`.

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `enabled` | bool | `false` | Master switch (env: `TRANSLATION_ENABLED`) |
| `endpoint` | string | `""` | OpenAI-compatible translator (env: `TRANSLATION_ENDPOINT`) |
| `model` | string | `tencent/Hy-MT2-7B-FP8` | Model id sent to it (env: `TRANSLATION_MODEL`) |
| `timeout_seconds` | float | `15.0` | Per-request budget (env: `TRANSLATION_TIMEOUT`) |
| `debug_force_language` | string | `""` | Force a target language, bypassing the agent filter (env: `TRANSLATION_FORCE_LANG`) |
| `thalabus.enable_only_for_agents` | list | — | Agent allow-list, e.g. `["AgentAssistant"]` |

### Checkpoint Boundaries

Automatically determined by content type:

| Content Type | Checkpoint Trigger |
|--------------|-------------------|
| JSON | At closing `]` or `}` of complete items |
| XML | At each closing `>` of any tag |
| HTML | At semantic closing tags (`</p>`, `</li>`, `</tr>`, `</div>`) |
| Plain Text | Every 250 tokens OR at paragraph boundaries |

## Environment Variables

Only the following are read (§"Overview": env → config.json → default). Detector, recovery,
and history knobs have **no** env override; set them in `config.json`.

```bash
# Server (these two are read via os.getenv, so they always beat config.json)
DE2_HOST=127.0.0.1              # config: server.host
DE2_PORT=9000                   # config: server.port
DE2_PROTOCOL=https              # config: server.protocol
DE2_SSL_CERT=/path/to/cert.crt  # config: server.ssl_cert_path
DE2_SSL_KEY=/path/to/key.key    # config: server.ssl_key_path

# Upstream backend (base URL only — no /v1/... suffix)
VLLM_BASE_URL=http://192.168.30.31:8081

# Semantic verifier
SEMANTIC_LLM_ENABLED=true
SEMANTIC_VERIFIER_ENDPOINT=http://my-semantic:8350/v1/chat/completions
SEMANTIC_VERIFIER_MODEL=surogate/Qwen3.5-2B-FP8
SEMANTIC_VERIFIER_TIMEOUT=10.0
SEMANTIC_MAX_MODEL_LEN=1024

# UI
DE2_UI_ENABLED=true
DE2_UI_SHOW_CONTENT=false

# Translation
TRANSLATION_ENABLED=true
TRANSLATION_ENDPOINT=http://translator:8351/v1/chat/completions
TRANSLATION_MODEL=tencent/Hy-MT2-7B-FP8
TRANSLATION_TIMEOUT=15.0
TRANSLATION_FORCE_LANG=de       # debug: force a target language, bypasses the agent filter
```

Not supported despite appearing elsewhere in these docs: `LOG_LEVEL`,
`DE2GENERATOR_HOST`/`_PORT`/`_CONFIG`, `VLLM_ENDPOINT`, `SEMANTIC_ENDPOINT`,
`ENTROPY_MIN_HEALTHY`, `REPETITION_*`, `MAX_RECOVERY_ATTEMPTS`, `MAX_FULL_RESTARTS`,
`UI_MAX_HISTORY`.

## Example Configurations

> ⚠️ The four examples below still use a pre-rename schema. Read them through this mapping:
> `model_endpoint` → top-level `vllm.base_url` (base URL only), `entropy.min_block_chars` →
> `entropy.min_chars`, `recovery.checkpoint_interval_tokens` →
> `recovery.plain_text_checkpoint_interval`, `semantic_llm.cooldown_minutes` →
> `thinking.cooldown_duration_seconds`. See [Verified key reference](#verified-key-reference).

### Development (Local Testing)

```json
{
  "model_endpoint": "http://localhost:8000/v1/chat/completions",
  "semantic_llm": {
    "enabled": false
  },
  "detection": {
    "entropy": {
      "min_healthy": 3.5,
      "max_healthy": 7.0
    }
  },
  "recovery": {
    "max_attempts": 2
  }
}
```

### Production (Aggressive Detection)

```json
{
  "model_endpoint": "http://internal-vllm:8000/v1/chat/completions",
  "semantic_llm": {
    "enabled": true,
    "endpoint": "http://internal-verifier:8350/v1/chat/completions",
    "model": "qwen2.5-7b-instruct",
    "timeout_seconds": 10.0
  },
  "detection": {
    "entropy": {
      "min_healthy": 3.2,
      "max_healthy": 7.5,
      "min_block_chars": 50
    },
    "repetition": {
      "window_tokens": 200,
      "critical_score": 30,
      "warning_score": 50
    }
  },
  "recovery": {
    "max_attempts": 5,
    "max_full_restarts": 2,
    "checkpoint_interval_tokens": 100
  },
  "ui": {
    "max_history": 200
  }
}
```

### High-Throughput (Speed-Focused)

```json
{
  "model_endpoint": "http://fast-vllm:8000/v1/chat/completions",
  "semantic_llm": {
    "enabled": false
  },
  "detection": {
    "entropy": {
      "min_healthy": 2.0,
      "max_healthy": 8.0,
      "min_block_chars": 500
    }
  },
  "recovery": {
    "max_attempts": 1,
    "checkpoint_interval_tokens": 500
  },
  "ui": {
    "max_history": 20
  }
}
```

### Semantic-Heavy (Quality-Focused)

```json
{
  "model_endpoint": "http://vllm:8000/v1/chat/completions",
  "semantic_llm": {
    "enabled": true,
    "endpoint": "http://semantic-verifier:8350/v1/chat/completions",
    "model": "qwen3.5-32b",
    "timeout_seconds": 15.0,
    "min_block_chars": 100,
    "min_failing_categories": 1
  },
  "detection": {
    "entropy": {
      "min_healthy": 4.0,
      "max_healthy": 6.5
    }
  },
  "recovery": {
    "max_attempts": 3
  }
}
```

## Tuning Guide

### Increasing Detection Sensitivity

Make the system reject more output:
1. Lower `entropy.min_healthy` (e.g., 3.2 → 3.0)
2. Raise `entropy.max_healthy` (e.g., 7.0 → 6.5) — catches high-entropy gibberish
3. Lower `repetition.critical_score` (e.g., 40 → 25)
4. Increase `semantic_llm.enabled` with lower `min_failing_categories`
5. Reduce `recovery.plain_text_checkpoint_interval` for more frequent validation

### Decreasing Detection Sensitivity

Allow more output through (fewer false positives):
1. Raise `entropy.min_healthy` (e.g., 3.5 → 4.0)
2. Lower `entropy.max_healthy` (e.g., 7.0 → 7.5)
3. Raise `repetition.critical_score` (e.g., 40 → 60)
4. Disable semantic verification or increase `min_failing_categories`
5. Increase `recovery.plain_text_checkpoint_interval`

### Speeding Up Processing

Reduce latency:
1. Disable semantic verifier (`enabled: false`)
2. Increase `detection.entropy.min_chars` (fewer calculations)
3. Increase `repetition.window_tokens`
4. Increase `recovery.plain_text_checkpoint_interval`
5. Disable detailed logging

### Improving Accuracy

Better degeneration detection:
1. Enable semantic verifier with small model
2. Lower `detection.entropy.min_chars` for faster detection
3. Reduce `recovery.plain_text_checkpoint_interval` for granular validation
4. Tune entropy bounds for your use case (test with real outputs)
5. Lower `semantic_llm.timeout_seconds` for responsive failures

## config.json Location

The server looks for `config.json` in the project root directory:

```
de2generator/
├── config.json       ← Loaded by default
├── src/
└── doc/
```

If not found, defaults are used and a warning is logged:
```
WARNING:de2generator:Could not load config.json: [Errno 2] No such file or directory: '...config.json'. Using hardcoded defaults.
```

## Verified key reference

The complete, current top-level schema, as read from `src/core/constants.py` and the deployed
`config.json` on 2026-11-14. Anything in this file that disagrees with this list is stale.

```jsonc
{
  "vllm":       { "base_url", "prefill_timeout_seconds", "stream_stall_timeout_seconds" },
  "semantic_llm": { "enabled", "endpoint", "model", "timeout_seconds", "max_model_len",
                  "max_answer_tokens", "safety_buffer_tokens", "min_block_chars",
                  "min_failing_categories" },
  "detection":  { "entropy": { "min_healthy", "max_healthy", "min_chars" },
                  "repetition": { "window_tokens", "critical_score", "warning_score" },
                  "thinking_loop": { "min_chars", "max_repeats" } },
  "thinking":   { "chars_per_token", "budget_exhaustion_threshold", "cooldown_duration_seconds" },
  "recovery":   { "max_attempts", "max_full_restarts", "plain_text_checkpoint_interval" },
  "translation":{ "enabled", "endpoint", "model", "timeout_seconds",
                  "thalabus": { "enable_only_for_agents" } },
  "ui":         { "enabled", "max_history", "show_content" },
  "server":     { "protocol", "host", "port", "ssl_cert_path", "ssl_key_path" }
}
```

Keys that do **not** exist anywhere: `model_endpoint` (it is a per-request body field under
`de2generator.model_endpoint`, consumed and stripped before forwarding), `listen_host`,
`listen_port`, `detection.*.enabled`, `entropy.min_block_chars`,
`recovery.checkpoint_interval_tokens`, `semantic_llm.cooldown_minutes`,
`ui.thinking_chars_per_token`.

## Runtime Config Validation

On startup, the server logs:
```
INFO:de2generator:Loaded configuration from config.json
INFO:de2generator:Semantic LLM enabled: True
INFO:de2generator:Entropy detection: min=3.5, max=7.0, min_chars=100
INFO:de2generator:Listening on 0.0.0.0:8356
```

Check for `ERROR` level logs if configuration is invalid.

## Secrets Management

**Do not commit `config.json` with sensitive data to version control.**

For production:
1. Store `config.json` separately
2. Use environment variables for sensitive values (endpoints, model names)
3. Mount at runtime: `docker run -v /path/to/config.json:/app/config.json ...`
4. Use ConfigMaps in Kubernetes

Example secure deployment:
```yaml
# Kubernetes ConfigMap
apiVersion: v1
kind: ConfigMap
metadata:
  name: de2generator-config
data:
  config.json: |
    {
      "model_endpoint": "${VLLM_ENDPOINT}",
      "semantic_llm": {
        "endpoint": "${SEMANTIC_ENDPOINT}"
      }
    }
---
# Pod with environment variable injection
env:
- name: VLLM_ENDPOINT
  valueFrom:
    configMapKeyRef:
      name: de2generator-secrets
      key: vllm-url
```
