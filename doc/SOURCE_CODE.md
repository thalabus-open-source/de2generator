# Source Code Structure

## Overview

The codebase is organized into logical modules following clean architecture principles. Each module has a single responsibility and minimal dependencies on others.

```
src/
├── de2generator.py          # Main FastAPI app + request handler
├── core/                    # Data models & configuration
├── detection/               # Quality detector implementations
├── recovery/                # Rollback & retry logic
├── streaming/               # vLLM client integration
├── ui/                      # Real-time event broadcasting
├── utils/                   # Cross-cutting utilities
├── static/                  # Web UI assets (HTML, CSS, JS modules)
└── test/                    # Unit tests
```

## Core Module (`src/core/`)

### models.py (Data Classes)

Defines immutable data structures for type safety and clarity.

**GenerationSession:**
```python
@dataclass
class GenerationSession:
    request_id: str              # Unique per request
    model: str                   # Model name (e.g., "Qwen/Qwen3.5-35B")
    original_request: dict       # Full incoming request
    generated_text: str          # Accumulated output tokens
    thinking_text: str           # Reasoning (extended thinking models)
    token_count: int             # Total tokens generated
    checkpoints: list[Checkpoint]# All checkpoints (healthy + rejected)
    metrics: dict                # Telemetry (attempts, recovery_count, etc.)
    recovery_attempts: int       # How many rollbacks occurred
    should_log_interaction: bool # Flag for telemetry logging
    # ... 18 fields total
```

**Checkpoint:**
```python
@dataclass
class Checkpoint:
    token_position: int               # Position in stream
    generated_text: str               # Content at this checkpoint
    metrics_snapshot: dict            # Token counts, TPS at this point
    entropy: float                    # Shannon entropy score
    entropy_status: str               # "ok" | "candidate_reject" | "rejected"
    semantic_results: dict            # Semantic detector results
    content_type: str                 # "json" | "xml" | "html" | "text"
    recovery_target: Optional[int]    # Points to checkpoint to rollback to
```

**DetectionResult:**
```python
@dataclass
class DetectionResult:
    score: float          # 0.0-100.0, higher = worse
    severity: str         # "ok" | "candidate_reject" | "rejected"
    reason: str           # Human-readable explanation
```

### config.py (Configuration Loading)

Loads and provides typed access to `config.json`.

**Key Functions:**

- `load_config(config_path)` - Loads JSON, stores in global `_config`, returns dict
- `get_config_value(key_path, env_var, default)` - Type-safe nested access with env override

**Example:**
```python
load_config()  # Loads config.json from project root

# Access with dot-path
entropy_min = get_config_value("detection.entropy.min_healthy", 
                               "ENTROPY_MIN_HEALTHY",
                               default=3.5)
```

### constants.py (All Tunable Parameters)

Centralizes all threshold values for easy modification.

**Import structure:**
```python
# Detection thresholds
ENTROPY_MIN_HEALTHY = get_config_value("detection.entropy.min_healthy", ..., 3.5)
ENTROPY_MAX_HEALTHY = get_config_value("detection.entropy.max_healthy", ..., 7.0)

# Repetition thresholds
REPETITION_WINDOW_TOKENS = get_config_value("detection.repetition.window_tokens", ..., 100)
REPETITION_CRITICAL_SCORE = get_config_value("detection.repetition.critical_score", ..., 40)

# Recovery limits
MAX_RECOVERY_ATTEMPTS = get_config_value("recovery.max_attempts", ..., 3)
MAX_FULL_RESTARTS = get_config_value("recovery.max_full_restarts", ..., 1)

# Etc.
```

**Usage:** Import constants in any module:
```python
from core.constants import ENTROPY_MIN_HEALTHY, MAX_RECOVERY_ATTEMPTS
```

## Detection Module (`src/detection/`)

Each detector is a pluggable class implementing quality validation.

### entropy.py (Shannon Entropy)

```python
class EntropyDetector:
    def detect(self, text: str, checkpoint_idx: int) -> DetectionResult:
        """Analyze character distribution entropy."""
        entropy_bits = calculate_shannon_entropy(text)
        
        if ENTROPY_MIN_HEALTHY <= entropy_bits <= ENTROPY_MAX_HEALTHY:
            return DetectionResult(score=0.0, severity="ok", reason="...")
        else:
            return DetectionResult(score=50.0, severity="candidate_reject", reason="...")
```

**Three-Strike Logic:**
```python
detector.get_rollback_target()  # Returns checkpoint before first strike
detector.increment_strike()      # Mark another anomaly
```

### repetition.py (Token Repetition)

Analyzes token patterns in a rolling window.

```python
class RepetitionDetector:
    def detect(self, text: str) -> DetectionResult:
        """Calculate health score from token diversity metrics."""
        distinct_ratio = len(unique_tokens) / total_tokens
        bigram_ratio = repeated_bigrams / total_bigrams
        trigram_ratio = repeated_trigrams / total_trigrams
        
        health_score = (distinct_ratio * 0.40 + 
                       (1 - bigram_ratio) * 0.35 + 
                       (1 - trigram_ratio) * 0.25) * 100
```

### semantic.py (LLM-Based Quality)

Uses async background thread pool to call external verifier LLM.

```python
class SemanticProgressDetector:
    async def submit(self, local_cp_idx: int, new_block: str, 
                     context: str, global_cp_idx: int,
                     ui_update_callback: Callable):
        """Queue checkpoint for async validation."""
        # Non-blocking: returns immediately
        # Result arrives asynchronously via callback
        
class SemanticLLMAvailabilityTracker:
    """Manages cooldown on verifier failures."""
    def is_available() -> bool:
        # Returns False if in cooldown period
    
    def mark_failure():
        # Increment failure counter, start cooldown if 3 strikes
```

**Architecture:**
- ThreadPoolExecutor with 1-2 workers
- Queue-based: checkpoints are queued, results are non-blocking
- Timeout: 5 seconds per request (configurable)
- Cooldown: 5 minutes after 3 consecutive failures
- Out-of-order result handling: uses checkpoint position order, not arrival order

### content_type.py (Format Detection)

Identifies JSON, XML, HTML, or plain text and determines checkpoint boundaries.

```python
def detect_content_type(text: str) -> str:
    """Returns "json" | "xml" | "html" | "text"."""
    
HTML_CHECKPOINT_TAGS = {"p", "li", "tr", "div"}  # Semantic boundaries
JSON_CLOSING = {"]", "}"}                         # Complete items
```

## Recovery Module (`src/recovery/`)

Handles rollback and retry strategies.

### checkpoint_manager.py (State Machine)

Tracks checkpoint states and implements three-strike rule.

```python
class CheckpointManager:
    def should_checkpoint(self) -> bool:
        """Check if it's time to create a new checkpoint."""
        
    def health_check(self, detection_results: list) -> str:
        """Determine if checkpoint is OK or REJECTED."""
        # Returns "ok" | "candidate_reject" | "rejected"
        
    def get_last_checkpoint_entropy(self) -> float:
        """Get entropy of most recent checkpoint."""
        
    def increment_checkpoint_count(self) -> int:
        """Mark another checkpoint processed."""
        
    def get_rollback_target(self) -> Optional[int]:
        """Return checkpoint index to resume from."""
```

**Three-Strike Implementation:**
```python
strike_count = {}  # Per detector per checkpoint

if detection_result.severity == "candidate_reject":
    strike_count[checkpoint_id] += 1
    if strike_count[checkpoint_id] >= 3:
        checkpoint.severity = "rejected"
        return get_rollback_target()
```

### message_builders.py (Retry Strategies)

Constructs prompts for different recovery scenarios.

```python
def is_thinking_exhausted(thinking_text: str, response_text: str, 
                         thinking_budget_tokens: int) -> bool:
    """Check if model exceeded thinking token limit."""
    
def build_continuation_messages(session: GenerationSession) -> list:
    """Build prompt: continue from last checkpoint."""
    
def build_thinking_resume_messages(session: GenerationSession) -> list:
    """Build prompt: resume with deeper reasoning."""
    
def build_nothink_retry_messages(session: GenerationSession) -> list:
    """Build prompt: disable thinking, retry."""
```

## Streaming Module (`src/streaming/`)

Handles token streaming from vLLM backend.

### vllm_client.py (vLLM Integration)

Async HTTP client for streaming responses.

```python
async def stream_from_vllm(request_data: dict, session_id: str, 
                          vllm_endpoint: str):
    """Stream tokens from vLLM backend.
    
    Yields:
        (content_chunk, reasoning_chunk, token_count, reasoning_tokens)
    """
    
    # Opens SSE connection to vLLM
    # Parses JSON chunk format
    # Extracts reasoning text if extended thinking
    # Handles connection errors
```

**Token Parsing:**
```json
{
  "choices": [{
    "delta": {
      "content": "The answer is",
      "reasoning_content": "Let me think..."
    }
  }],
  "usage": {
    "completion_tokens": 42
  }
}
```

## UI Module (`src/ui/`)

Real-time event broadcasting via Server-Sent Events (SSE).

### events.py (Event System)

```python
def ui_start_interaction(request_id: str, model: str):
    """Broadcast: new generation started."""
    
def ui_add_checkpoint(request_id: str, checkpoint: Checkpoint):
    """Broadcast: new checkpoint created."""
    
def ui_update_checkpoint_semantic(request_id: str, checkpoint_id: int,
                                 semantic_results: dict):
    """Broadcast: semantic validation result arrived."""
    
async def ui_events_endpoint(request: Request) -> StreamingResponse:
    """SSE endpoint: /ui/events"""
    # Yields event-stream formatted JSON
    # Clients reconnect automatically
```

**Event Format (Server-Sent Events):**
```
data: {"type": "checkpoint", "data": {...}}

data: {"type": "stats_update", "data": {"tokens": 42, "tps": 2.5}}

data: {"type": "interaction_complete", "data": {...}}
```

## Utils Module (`src/utils/`)

Cross-cutting concerns.

### logging.py (Telemetry)

Logs LLM interactions for debugging and analysis.

```python
def log_llm_interaction(request_data: dict, overthinking_text: str,
                       final_answer: str, request_id: str) -> str:
    """Write structured JSON to log file.
    
    Returns:
        Path to created log file: log_llm_interaction_YYYYMMDD_HHMMSS_mmm_XXXX.txt
    """
    
    # Creates atomic filename with sequence number
    # Writes JSON with: request, reasoning, response
```

### terminal.py (Console Output)

ANSI-colored formatted output for debugging.

```python
def print_block(label: str, ok: bool, text: str, note: str = ""):
    """Print [OK] or [REJECT] block."""
    # Colored: green for OK, red for REJECT
    
def print_thinking_block(label: str, text: str):
    """Print [THINKING] block."""
    # Colored: blue
```

## Static UI (`src/static/`)

### HTML/CSS

- `ui.html` - Single-page app shell
- `styles/ui.css` - Responsive styling (dark/light themes)

### JavaScript Modules (ES6)

All modules use ES6 import/export syntax.

**Core:**
- `main.js` - Initialization, window scope binding
- `config.js` - Config loading from `/ui/config.json`
- `state.js` - DOM refs, in-memory store (Map)
- `connection.js` - SSE connection management with auto-reconnect

**Features:**
- `utils/dom.js` - HTML escape, text truncation, scroll helpers
- `utils/theme.js` - Dark/light theme toggle, localStorage persistence
- `events/handlers.js` - SSE event dispatch (checkpoint, stats, completion)
- `rendering/card.js` - Card rendering logic (240+ lines)

**Absolute Import Paths:**
```javascript
// All imports use /ui/scripts/ prefix
import { loadConfig } from '/ui/scripts/config.js';
import { initTheme } from '/ui/scripts/utils/theme.js';
```

This avoids relative path resolution issues when served from `/ui/app.js`.

## HTTP Layer (`src/api/`)

One router per surface; no business logic lives here.

| Module | Routes | Notes |
|--------|--------|-------|
| `paths.py` | — | `SRC_DIR`, `STATIC_DIR`, `LOGS_DIR` (relative to the working directory, as the Dockerfile sets `WORKDIR /app`) |
| `system.py` | `GET /health`, `GET /health/upstream`, `GET /v1/models` | liveness probe (what the healthcheck targets); backend diagnostic that never gates; model list proxied from the backend |
| `chat.py` | `POST /v1/chat/completions`, `POST /v1/cancel` | cancellation registry, interaction log, error mapping |
| `webui.py` | `/ui`, `/ui/{static}`, `/ui/config.json`, `/ui/logprobs`, `/ui/update-translation`, `/ui/events` | runtime toggles only |
| `stats.py` | `/ui/stats`, `/ui/stats/{hourly,totals}`, `POST /ui/stats/reset-totals`, `/logs/{filename}` | token dashboard + log browser |

`ROUTERS` in `api/__init__.py` is the single mount list used by `create_app()`.

**Error mapping:** duplicate cancellation token → `409`; cancellation before/after a
live run → `200 {"status":"not_found"}`; malformed token → `400`; a cancelled
generation → `499`; backend unreachable → `502`; backend timeout → `504`; anything
else → `500` with the exception logged.

## Orchestration Layer (`src/orchestration/`)

| Module | Responsibility |
|--------|----------------|
| `supervisor.py` | `GenerationSupervisor`: the stream/checkpoint/detect/recover loop and the post-stream decision table |
| `request_context.py` | Parse the body once: identity, model parameters (whitelisted), thinking budget, translation target |
| `cancellation.py` | Token validation, the in-flight registry, `run_cancellable_generation` |
| `telemetry.py` | Structured per-verdict telemetry records |

`supervise_generation()` is the only entry point the HTTP layer calls. The supervisor
reads module-level `MAX_RECOVERY_ATTEMPTS` / `MAX_FULL_RESTARTS` at call time so tests
can shrink the ladder without patching behaviour.

**Decision table** (evaluated in this order at the end of every attempt):
aborted → thinking loop exceeded → thinking budget exceeded/answered → empty after
thinking → degenerated → empty. Each verdict maps to `RETRY` (continue from the last
committed checkpoint), `FULL_RESTART` (clear state, resend the original), `COMPLETE`
or `RETURN_LAST`.

## Response Layer (`src/response/`)

`builder.build_response(session, meta)` renders the accumulated checkpoints into the
OpenAI completion object, attaches the `de2generator` metadata block (timings, token
split, recovery counters), clips retained logprobs to the window the client can read,
and applies translation when one was resolved.

## Translation Layer (`src/translation/`)

`applier.py` decides *whether* a response is translated (config flag + agent allow
list + request override) and `merging.py` rebuilds the message list so a bilingual
assistant message keeps its thinking in the source language and its answer in the
target language. `translator.py` remains the HTTP client for the translation service.

**Main Supervision Loop:**
```python
async def supervise_generation(request_data: dict) -> Tuple[dict, GenerationSession]:
    session = GenerationSession(...)
    
    async for content_chunk, reasoning_chunk, tokens, reason_tokens in stream_from_vllm(...):
        session.generated_text += content_chunk
        session.thinking_text += reasoning_chunk
        
        if should_checkpoint(...):
            checkpoint = create_checkpoint(...)
            
            # Run detectors
            entropy_result = entropy_detector.detect(...)
            semantic_result = semantic_detector.submit(...)  # async
            
            # Three-strike logic
            if is_rejected(checkpoint):
                rollback_target = get_rollback_target(...)
                # Retry generation from checkpoint
                
            ui_broadcast_checkpoint(checkpoint)
    
    return build_response(session), session
```

## Testing (`src/test/`)

Tests run against a scripted backend on isolated debug ports, never against the
production instance. `debug_stack.py` starts the mock and the proxy, waits for both
to answer, runs `pytest`, and shuts everything down again.

| File | Style | Covers |
|------|-------|--------|
| `mock_llm_backend.py` | test double | OpenAI-compatible SSE/JSON backend; scenario chosen by the `model` field; records what the proxy sent |
| `debug_stack.py` | runner | `serve` (foreground) / `test` (pytest); refuses busy debug ports, warns when 8356 is in use |
| `test_debug_server.py` | HTTP integration | routes, status mapping, SSE, cancellation, translation, dashboard, log guards |
| `test_supervision_scenarios.py` | unit, scripted stream | the supervisor's decision table: rollback, restarts, thinking budget, aborts |
| `test_request_context.py` | unit | body parsing, agent/translation resolution, parameter whitelist |
| `test_response_builder.py` | unit | response shape, token accounting, logprob clipping |
| `test_cancellation.py` | unit | token validation and the in-flight registry |
| `test_health_probe.py` | unit, offline ASGI | `/health` stays green with a dead backend; `/health/upstream` reports it instead of failing |
| `test_streaming.py` | unit | vLLM client retry/stall handling |
| `de2generator_test.py` | legacy probe | end-to-end against a live deployment (skipped when offline) |

```bash
python src/test/debug_stack.py test            # everything
python src/test/debug_stack.py test test_debug_server.py -v -k degeneration
```

## Dependency Graph

```
de2generator.py (entry point: app factory, SSL, CLI, uvicorn)
└─ api/            (routes: HTTP surface only)
   └─ orchestration/ (supervisor, request context, cancellation, telemetry)
      ├─ detection/   (entropy, repetition, semantic, content_type)
      ├─ recovery/    (checkpoint_manager, message_builders, guards)
      ├─ translation/ (applier → translator, merging)
      ├─ streaming/   (vllm_client)
      └─ response/    (builder)
         ↑
core/ (models, config, constants, endpoints) and utils/ (logging, terminal)
   shared by every layer; ui/ (event bus) is fed by the supervisor and read by api/webui
```

Dependencies point inwards and downwards: `core/` and `utils/` import nothing from the
layers above them, and no lower layer imports `api/`.

## Adding New Features

### Add a New Detector

1. Create `src/detection/my_detector.py`
2. Implement `class MyDetector` with `detect(text, checkpoint_idx) -> DetectionResult`
3. Add to `src/detection/__init__.py`
4. Add constants to `src/core/constants.py`
5. Integrate in `de2generator.py` main loop

### Add a New Checkpoint Boundary Type

1. Add format detection to `src/detection/content_type.py`
2. Add boundaries to `detect_content_type()`
3. Test with sample content

### Add UI Feature

1. Create JS module in `src/static/scripts/`
2. Export functions
3. Import in `main.js` with `/ui/scripts/` prefix
4. Update HTML/CSS as needed
5. Connect SSE event handler if needed

## Code Quality

- **Type hints:** All functions annotated (Python 3.13+)
- **Docstrings:** Google-style for all public functions
- **Logging:** Per-module loggers with context (request_id, checkpoint_id)
- **Error handling:** Graceful degradation, detailed error messages
- **Testing:** Unit tests for detectors and state machines

## Performance Considerations

- **Entropy calculation:** O(n) where n = text length
- **Semantic validation:** Async, non-blocking, ~100-500ms per checkpoint
- **Three-strike logic:** O(1) state updates
- **Memory:** ~10MB per active session
- **Throughput:** 10+ concurrent sessions at 20 tokens/sec each

## Documentation

- **ARCHITECTURE.md** - System design, components, data flow
- **CONFIGURATION.md** - config.json reference, tuning guide
- **DETECTION_STRATEGY.md** - Detailed detector algorithms
- **API.md** - REST endpoint specifications
