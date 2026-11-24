# de²generator — Generation Supervisor

A transparent OpenAI-compatible proxy between Thalabus and vLLM that detects LLM output degeneration in real time and recovers automatically.

> **Deployment:** runs on `thalabus-ai-1`. Every Thalabus instance — debug instances on
> laptops and the demonstration instances on `thalabus-ai-1` / `thalabus-ai-2` — talks to the
> LiteLLM gateway *through* this proxy, so it is on the request path, not an optional extra.
> Details and tuning: [`doc/DEPLOYMENT.md`](doc/DEPLOYMENT.md#verified-deployment-thalabus-fleet),
> [`doc/CONFIGURATION.md`](doc/CONFIGURATION.md#verified-key-reference).

## Quick Start

### Prerequisites
- Python 3.13+
- vLLM backend or compatible LLM server
- (Optional) Semantic verifier LLM for advanced quality checks

### Installation

```bash
# Clone and install
git clone <repo>
cd de2generator
python -m venv .venv
.venv\Scripts\activate  # or source .venv/bin/activate on Linux/macOS
pip install -r requirements.txt

# Configure: edit config.json in place (it is tracked in this repo;
# there is no config.example.json template). Defaults live in src/core/constants.py.
```

### Running

```bash
# Local development
python src/de2generator.py
python src/de2generator.py --vllm-url http://192.168.30.31:8081   # override the backend

# Docker
docker build -t de2generator:latest .
docker run -p 8356:8356 -v $(pwd)/config.json:/app/config.json de2generator:latest

# Compose (network_mode: host, so no port mapping needed)
docker compose up -d
```

The container healthcheck probes `/health` — liveness only. It deliberately does **not**
probe the proxied `GET /v1/models`: a non-200 there describes the *backend*, and restarting
this proxy cannot fix a backend (it would turn a clean `502` into an outage). It also could
never pass against an authenticated gateway, which answers `401` to an unauthenticated
model list. Ask instead: **`GET /health/upstream`** reports `base_url`, `reachable`,
`status_code` and `error` — any HTTP response counts as reachable, so `401` reads as
"upstream present, credentials needed" rather than "down". Alert on that, not on the badge.

Visit the **web UI** at **http://localhost:8356/ui** to monitor generation in real time.

## Features

✅ **Real-Time Degeneration Detection** — Entropy analysis, repetition detection, optional LLM-based semantic validation

✅ **Automatic Recovery** — Rolls back to last healthy checkpoint, injects continuation prompt, retries (up to 3 attempts)

✅ **Three-Strike Confirmation** — Requires 3 consecutive anomalies before triggering recovery (prevents false positives)

✅ **Live Dashboard** — Real-time checkpoint monitoring with entropy badges, quality scores, token metrics (Server-Sent Events)

✅ **Modular Architecture** — Clean separation of detection, recovery, streaming, and UI concerns

✅ **Transparent Proxy** — Drop-in replacement for direct vLLM calls, fully OpenAI-compatible

## How It Works

```
1. Request streams from thalabus → de²generator
2. de²generator proxies to vLLM (always streaming)
3. As tokens arrive:
   • Buffer into logical checkpoints (natural boundaries)
   • Run multiple detectors (entropy, repetition, semantic)
   • Apply three-strike rule (3 anomalies = rejection)
4. If checkpoint REJECTED:
   • Rollback to last healthy checkpoint
   • Inject continuation prompt
   • Retry generation
5. Response returned to thalabus
6. UI updated in real-time via SSE
```

## Architecture

```
thalabus
    │ (OpenAI-compatible API)
    ▼
de²generator
  ├─ Stream from vLLM
  ├─ Create checkpoints (natural boundaries)
  ├─ Run detectors (entropy, repetition, semantic async)
  ├─ Apply three-strike logic
  ├─ Auto-rollback + retry on rejection
  └─ Broadcast events to UI (SSE)
    │
    ▼
vLLM Backend
    │
    ├─ Primary LLM (Qwen, Llama, etc.)
    │
    └─ Semantic Verifier LLM (optional, small + fast)
```

## Project Structure

```
de2generator/
├── README.md               # This file (GitHub overview)
├── doc/                    # Detailed documentation
│   ├── ARCHITECTURE.md     # System design & components
│   ├── CONFIGURATION.md    # config.json reference & tuning guide
│   ├── SOURCE_CODE.md      # Module structure & key files
│   ├── DETECTION_STRATEGY.md  # Detector algorithms & three-strike logic
│   ├── API.md              # REST endpoints & formats
│   └── DEPLOYMENT.md       # Docker, environments, production tips
├── src/
│   ├── de2generator.py     # Entry point: app factory, SSL, CLI, uvicorn
│   ├── api/                # HTTP layer: one router per surface
│   │   ├── paths.py        #   filesystem roots (static, logs)
│   │   ├── system.py       #   /health, /health/upstream, /v1/models
│   │   ├── chat.py         #   /v1/chat/completions, /v1/cancel
│   │   ├── webui.py        #   dashboard assets, config, runtime toggles
│   │   └── stats.py        #   token-usage dashboard + log browser
│   ├── orchestration/      # The supervision loop itself
│   │   ├── supervisor.py   #   GenerationSupervisor: stream, detect, recover
│   │   ├── request_context.py # request parsing + parameter whitelist
│   │   ├── cancellation.py # cancellation tokens and the in-flight registry
│   │   └── telemetry.py    #   per-request session accounting
│   ├── response/           # OpenAI-shaped response assembly
│   ├── core/               # Data models, config, constants, endpoint registry
│   ├── detection/          # Entropy, repetition, semantic, content-type
│   ├── recovery/           # Checkpoint manager, message builders, guards
│   ├── translation/        # Target resolution, bilingual merging, applier
│   ├── streaming/          # vLLM client integration
│   ├── ui/                 # Event broadcasting (SSE)
│   ├── utils/              # Logging, terminal formatting
│   ├── static/             # Web UI (HTML, CSS, ES6 modules)
│   └── test/               # Mock backend, unit + integration suites
├── logs/                   # Interaction logs (gitignored)
├── config.json             # Configuration (tracked here, mounted read-only in Docker)
├── docker-compose.yml      # Optional service (host networking, env-driven backend URL)
├── Dockerfile              # Container definition
└── requirements.txt        # Python dependencies
```

Dependencies point inwards: `api/` calls `orchestration/`, which drives
`detection/`, `recovery/`, `translation/` and `streaming/`; nothing imports `api/`.

## Documentation

Detailed documentation is in the `doc/` directory:

- **[ARCHITECTURE.md](doc/ARCHITECTURE.md)** — High-level system design, components, data flow
- **[CONFIGURATION.md](doc/CONFIGURATION.md)** — config.json reference with all tunable parameters and example configurations
- **[SOURCE_CODE.md](doc/SOURCE_CODE.md)** — Module organization, key files, code structure
- **[DETECTION_STRATEGY.md](doc/DETECTION_STRATEGY.md)** — Detailed explanation of detectors and three-strike logic
- **[API.md](doc/API.md)** — REST endpoint specifications and request/response formats
- **[DEPLOYMENT.md](doc/DEPLOYMENT.md)** — Docker, environment setup, production considerations

*Note:* This README is a quick overview for GitHub visitors. The `doc/` folder contains comprehensive technical documentation.

## Key Concepts

### Checkpoints
Natural intermediate states saved during generation (closing delimiters for JSON/XML/HTML, ~250 tokens for plain text). Only healthy checkpoints are retained.

### Detectors
- **Entropy Detector**: Measures character diversity (3.5-7.0 bits/byte = healthy)
- **Repetition Detector**: Analyzes token patterns (distinct/bigram/trigram ratios)
- **Semantic Detector**: Optional LLM-based quality assessment (asynchronous)

### Three-Strike Rule
Quality detectors require 3 consecutive anomalies to confirm degradation:
1. **Strike 1**: Checkpoint marked `CANDIDATE_REJECT` (warning)
2. **Strike 2**: Second anomaly recorded
3. **Strike 3**: Escalate to `REJECTED` → automatic rollback + retry

This prevents false positives from isolated anomalies.

### Recovery
On checkpoint rejection:
1. Rollback to last healthy checkpoint
2. Inject continuation prompt (maintains context)
3. Retry generation (up to 3 attempts)
4. Fall back to best attempt if all retries fail

## Web UI

Live monitoring dashboard at **http://localhost:8356/ui** displays:
- Real-time cards for each generation
- Checkpoint timeline with quality badges
- Thinking/reasoning text (extended thinking models)
- Token metrics (generated, TPS, elapsed, rejected)
- Theme toggle (dark/light)
- Live connection status

## Configuration

Edit `config.json`. Every key can be overridden by an environment variable, and the
built-in defaults live in `src/core/constants.py`. Precedence is
**env var → config.json → built-in default**, so an env var set by `docker-compose.yml`
(see its `VLLM_BASE_URL` default) wins over `vllm.base_url` in this file.

```json
{
  "vllm": {
    "base_url": "http://192.168.30.31:8081",
    "prefill_timeout_seconds": 600,
    "stream_stall_timeout_seconds": 300
  },
  "semantic_llm": {
    "enabled": true,
    "endpoint": "http://localhost:8350/v1/chat/completions",
    "model": "surogate/Qwen3.5-2B-FP8"
  },
  "detection": {
    "entropy": { "min_healthy": 3.5, "max_healthy": 7.0, "min_chars": 100 },
    "repetition": { "window_tokens": 100, "critical_score": 40, "warning_score": 70 },
    "thinking_loop": { "min_chars": 40, "max_repeats": 3 }
  },
  "recovery": {
    "max_attempts": 3,
    "max_full_restarts": 2,
    "plain_text_checkpoint_interval": 250
  },
  "translation": { "enabled": false },
  "ui": { "enabled": true, "max_history": 100, "show_content": true },
  "server": { "protocol": "http", "host": "0.0.0.0", "port": 8356 }
}
```

To pin a different backend for a single call, send a `de2generator` extension object in
the request body — it is consumed here and never forwarded to the backend:
`{"de2generator": {"model_endpoint": "http://other-host:8000", "agent_name": "…"}}`.

See [CONFIGURATION.md](doc/CONFIGURATION.md) for all options and tuning guide.

## Performance

- **Latency**: < 100ms per checkpoint (including entropy + detectors)
- **Memory**: ~10MB base + 10MB per active generation session
- **Throughput**: 10+ concurrent generations at 20 tokens/sec each

## Development

### Adding a New Detector

1. Create `src/detection/my_detector.py`
2. Implement `class MyDetector` with `detect(text, checkpoint_idx) -> DetectionResult`
3. Register in `src/detection/__init__.py`
4. Add constants to `src/core/constants.py`
5. Integrate in `de2generator.py`

See [SOURCE_CODE.md](doc/SOURCE_CODE.md) for detailed structure.

### Running Tests

The suite never touches the production instance: it runs a proxy on a debug port
against a scripted mock backend, on ports nothing else uses.

```bash
# Start the mock backend + debug proxy in the foreground (Ctrl-C stops both)
python src/test/debug_stack.py serve

# Run the suites against them (starts and stops the stack itself)
python src/test/debug_stack.py test
python src/test/debug_stack.py test test_debug_server.py -v -k degeneration
```

The runner refuses to start when 8398/8399 are busy and only warns when 8356 (the
production port) is in use, so a debug run can never displace a live service. Override
with `MOCK_LLM_PORT` / `DE2_DEBUG_PORT`; the tests read `DE2_TEST_BASE_URL` and
`DE2_TEST_MOCK_URL`.

| Suite | Covers |
|-------|--------|
| `test_debug_server.py` | HTTP contract: routes, status codes, SSE, cancellation, translation, dashboard |
| `test_supervision_scenarios.py` | The supervisor's decision table with a scripted stream |
| `test_request_context.py` | Request parsing, agent/translation resolution, parameter whitelist |
| `test_response_builder.py` | Response shape, token accounting, logprob clipping |
| `test_cancellation.py` | Token validation and the in-flight registry |
| `test_health_probe.py` | The `/health` ↔ `/health/upstream` split: a dead backend must never become a liveness failure |
| `test_streaming.py` | The vLLM client's retry and stall behaviour |
| `de2generator_test.py` | Legacy end-to-end probe against a live deployment (skipped offline) |

The mock backend chooses its behaviour from the `model` field, so a test picks a
scenario without touching the proxy: `mock/healthy`, `mock/json`, `mock/degenerate`
(prose that collapses into a loop), `mock/overthink`, `mock/thinkloop`, `mock/slow`,
`mock/empty`, `mock/error500`, `mock/nologprobs`. `GET /mock/requests` on the mock
returns what the proxy actually sent upstream, which is how the tests check forwarded
parameters, recovery prompts and endpoint overrides.

## Troubleshooting

| Issue | Check |
|-------|-------|
| UI shows "disconnected" | Server running? Port 8356 accessible? |
| Semantic errors in logs | Semantic verifier endpoint reachable? Model available? |
| Generation hanging | vLLM logs for errors; check connectivity |
| High memory usage | Monitor active sessions; each stores checkpoints |

See [DEPLOYMENT.md](doc/DEPLOYMENT.md) for more troubleshooting.

## Example Use Cases

### 1. Detecting Repetitive Output
A model generates: "...the answer is the answer is the answer is the answer is..."
- **Entropy Detector** catches low character diversity → `CANDIDATE_REJECT`
- **Repetition Detector** identifies token pattern → `CANDIDATE_REJECT`
- Third strike → **automatic rollback + retry**

### 2. JSON Generation Quality
A model generates malformed JSON: `{"name": "John", "age": 30, "city": "NYC",, ...}`
- **Content Detector** identifies JSON format
- **Structure Detector** finds syntax error → **instant rejection** (no strikes needed)
- **Automatic retry** from last valid JSON object

### 3. Semantic Degradation
A model generates: "...the point is that the point is important. The key point is that the point matters..."
- **Semantic Verifier** detects stagnation → `CANDIDATE_REJECT`
- Combined with entropy anomalies → **three-strike escalation**
- **Rollback + continuation** breaks the repetitive pattern

## Performance Tuning

### Faster Processing (less detection)
- Disable semantic verifier
- Increase entropy block size
- Increase checkpoint interval
- See [CONFIGURATION.md](doc/CONFIGURATION.md#speeding-up-processing)

### Better Accuracy (more detection)
- Enable semantic verifier
- Lower entropy thresholds
- Decrease checkpoint interval
- Lower entropy block size
- See [CONFIGURATION.md](doc/CONFIGURATION.md#improving-accuracy)

## License

[Add license information]

## Support

[Add contact/issue tracking information]

---

**For detailed technical documentation, see the [doc/](doc/) folder.**
