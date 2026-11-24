# Architecture Documentation

## System Overview

```
┌─────────────────┐
│    thalabus     │  Client sends OpenAI-compatible generation request
└────────┬────────┘
         │ POST /v1/chat/completions
         ▼
┌─────────────────────────────────────────────────────────┐
│           de²generator (Proxy & Supervisor)              │
│  ┌────────────────────────────────────────────────────┐ │
│  │ 1. Stream from vLLM (async with httpx)            │ │
│  │ 2. Buffer tokens, create checkpoints              │ │
│  │ 3. Run detectors in parallel (entropy, semantic)  │ │
│  │ 4. Apply three-strike confirmation logic          │ │
│  │ 5. Auto-rollback & retry if rejected              │ │
│  │ 6. Broadcast events to UI (Server-Sent Events)    │ │
│  └────────────────────────────────────────────────────┘ │
└────────┬────────────────────────────────────────────────┘
         │ Stream proxied to vLLM
         ▼
┌─────────────────────────────────────────────────────────┐
│              vLLM Backend (LLM Inference)                │
│  - Qwen, Llama, or other compatible models              │
│  - Streaming SSE responses                              │
└─────────────────────────────────────────────────────────┘
         ▲
         │ (Optional semantic quality checks)
         │
    ┌────┴──────────────────────┐
    │ Semantic Verifier LLM      │
    │ (Small, fast model)        │
    │ Async background thread    │
    └────────────────────────────┘
```

## Component Architecture

### 1. FastAPI Application (`de2generator.py`)

**Responsibilities:**
- Expose OpenAI-compatible REST endpoints
- Handle request/response lifecycle
- Orchestrate streaming supervision
- Manage HTTP/SSE connections

**Key Endpoints:**
- `POST /v1/chat/completions` - Main generation endpoint
- `GET /ui/events` - SSE stream for UI updates
- `GET /ui` - Web UI HTML
- `GET /ui/app.js` - UI entry point (ES6 module)
- `GET /ui/scripts/{path}` - UI module serving
- `GET /ui/config.json` - Frontend config

### 2. Streaming Layer (`src/streaming/`)

**vLLM Client** (`vllm_client.py`):
- Async HTTP client to vLLM backend
- Token-by-token streaming consumption
- Reasoning/thinking text extraction
- Error handling and timeouts

```python
async def stream_from_vllm(request_data, session_id, vllm_endpoint):
    # Yields: (content_chunk, reasoning_chunk, token_count, reasoning_tokens)
```

### 3. Detection Layer (`src/detection/`)

**Entropy Detector** (`entropy.py`):
- Shannon entropy calculation
- Healthy range: 3.5-7.0 bits/byte
- Three-strike confirmation
- Synchronous (instant results)

**Repetition Detector** (`repetition.py`):
- Token-level repetition analysis
- Bigram/trigram ratio tracking
- Weighted health score calculation
- Three-strike confirmation

**Semantic Detector** (`semantic.py`):
- External LLM-based quality assessment
- Asynchronous (background thread pool)
- Six quality categories (character repetition, word repetition, phrase repetition, semantic stagnation, poor structure, incoherence)
- Cooldown mechanism (5 min) on repeated failures
- Three-strike confirmation with out-of-order result handling

**Content Type Detector** (`content_type.py`):
- Identifies JSON, XML, HTML, or plain text
- Determines checkpoint boundaries per format

### 4. Recovery Layer (`src/recovery/`)

**Checkpoint Manager** (`checkpoint_manager.py`):
- Tracks healthy vs. unhealthy checkpoints
- Implements three-strike state machine
- Manages rollback targets

**Message Builders** (`message_builders.py`):
- Builds continuation prompts after rollback
- Thinking exhaustion detection
- Retry strategy selection (continuation, thinking-resume, no-think)

### 5. Data Models (`src/core/`)

**GenerationSession** (`models.py`):
```python
@dataclass
class GenerationSession:
    request_id: str
    model: str
    original_request: dict
    generated_text: str
    thinking_text: str
    token_count: int
    checkpoints: list[Checkpoint]
    metrics: dict
    recovery_attempts: int
    should_log_interaction: bool
    # ... 18 fields total
```

**Checkpoint** (`models.py`):
```python
@dataclass
class Checkpoint:
    token_position: int
    generated_text: str
    metrics_snapshot: dict
    entropy: float
    entropy_status: str  # "ok" | "candidate_reject" | "rejected"
    semantic_results: dict
    # ...
```

**Configuration** (`config.py`):
- Loads `config.json` at startup
- Provides typed access to settings
- Environment variable overrides
- Type conversion and validation

**Constants** (`constants.py`):
- All configuration thresholds
- Detector parameters
- Timeout and retry limits
- UI settings

### 6. UI Event System (`src/ui/`)

**Events Broadcaster** (`events.py`):
- In-memory event queue (deque, maxlen for history)
- Server-Sent Events (SSE) endpoint
- Real-time checkpoint updates
- Semantic validation results
- Generation completion events

**UI Components** (`src/static/scripts/`):

**Core modules:**
- `config.js` - Configuration loading
- `state.js` - DOM refs + in-memory store (Map)
- `connection.js` - SSE connection management with auto-reconnect

**Feature modules:**
- `utils/dom.js` - HTML escape, truncation, scroll helpers
- `utils/theme.js` - Dark/light theme toggle
- `events/handlers.js` - SSE event dispatch
- `rendering/card.js` - Interactive checkpoint cards

**Entry point:**
- `main.js` - Initialization and window scope binding

### 7. Utilities (`src/utils/`)

**Logging** (`logging.py`):
- LLM interaction logging (JSON structured)
- Request/response pairs with thinking text
- Atomic sequence numbering

**Terminal Output** (`terminal.py`):
- ANSI-colored console formatting
- [OK] / [REJECT] / [THINKING] block output
- Green/red status indicators

## Data Flow

### Generation Request

```
1. Client POST /v1/chat/completions
   ↓
2. Parse request → GenerationSession
   ↓
3. Call vLLM, start streaming
   ↓
4. For each token chunk:
   a. Buffer tokens
   b. Check if checkpoint boundary (content-type specific)
   c. Create Checkpoint
   d. Run detectors (entropy sync, semantic async)
   e. Apply three-strike logic
   f. Update Session state
   g. Broadcast UI event
   ↓
5. If REJECTED checkpoint:
   a. Calculate rollback target
   b. Build continuation prompt
   c. Retry generation (up to 3 times)
   ↓
6. Generation complete
   ↓
7. Return response to client
```

### UI Updates (SSE)

```
Main stream processing → Event emitted → Stored in queue
                              ↓
                        UI SSE endpoint
                              ↓
                    Send to all connected clients
                              ↓
                    Browser receives JSON
                              ↓
                    JS event handler processes
                              ↓
                    Render checkpoint card/update stats
```

## Concurrency Model

**Main Thread:**
- Token streaming from vLLM
- Checkpoint creation
- Entropy detection (synchronous)
- Three-strike state updates
- SSE client broadcasts

**Background Threads (ThreadPoolExecutor):**
- Semantic LLM validation (1-2 workers)
- Non-blocking: results update checkpoints asynchronously
- Timeouts: 5 seconds per request

**Browser (Web Worker potential future):**
- Currently single-threaded
- DOM updates from SSE events

## Configuration

See [CONFIGURATION.md](CONFIGURATION.md) for detailed settings.

Key areas:
- Detection thresholds (entropy min/max, repetition ratios)
- Semantic verifier endpoint and model
- Retry limits and timeouts
- Checkpoint intervals
- UI history size

## Error Handling

**vLLM Connection Failure:**
- Immediate 502 response to client
- Logged with details

**Semantic Verifier Failure:**
- Three consecutive failures → 5 min cooldown
- Graceful degradation: entropy detection continues
- Client UI shows warning

**Checkpoint Rejection:**
- Automatic retry (up to 3 attempts)
- Falls back to best attempt if all retries fail
- Logged as telemetry

**SSE Client Disconnect:**
- Gracefully handled
- UI auto-reconnects with exponential backoff

## Performance Characteristics

- **Entropy calculation:** < 1ms
- **Semantic validation:** 50-500ms (depends on verifier)
- **Checkpoint creation:** < 10ms
- **Three-strike state update:** < 1ms
- **Memory per session:** ~10MB (depends on checkpoint count and thinking text length)
- **Concurrent sessions:** 10+ at 20 tokens/sec each

## Extensibility

### Adding a New Detector

1. Create class in `src/detection/new_detector.py`
2. Implement `detect(text, checkpoint_idx)` → `DetectionResult`
3. Register in `src/detection/__init__.py`
4. Add constants to `src/core/constants.py`
5. Integrate in main supervision loop

### Adding a New Recovery Strategy

1. Create function in `src/recovery/message_builders.py`
2. Call from checkpoint rejection handler
3. Add telemetry/logging

### UI Customization

1. Modify `src/static/scripts/rendering/card.js` for card layout
2. Modify `src/static/styles/ui.css` for styling
3. Add new event handlers in `src/static/scripts/events/handlers.js`

## Testing

Unit tests in `src/test/de2generator_test.py`:
- Detector validation
- Three-strike logic
- Checkpoint transitions
- Message building

Integration tests:
- Full streaming from test vLLM
- Recovery scenario simulation
