# API Reference

## Endpoints

### `GET /health`

Liveness probe: `{"status":"ok","component":"de2generator"}`, always 200 while the process can
serve. Does not examine the backend — see `/health/upstream` for that.

### `GET /health/upstream`

Backend diagnostic (never gating): the resolved `base_url` plus `reachable` / `status_code` /
`error`. Any HTTP status counts as reachable, so `401` from an authenticated gateway is
reported rather than treated as down.

### `POST /v1/chat/completions`

Main generation endpoint (OpenAI-compatible).

**Request:**
```json
{
  "model": "Qwen/Qwen3.5-35B-A3B-FP8",
  "messages": [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is 2+2?"}
  ],
  "temperature": 0.7,
  "max_tokens": 100,
  "de2generator": {
    "model_endpoint": "http://custom-vllm:8000/v1/chat/completions",
    "agent_name": "my-agent",
    "goal_id": "goal-123",
    "cancellation_token": "unique-client-generated-token"
  }
}
```

**Parameters:**
- `model` (string, required): Model name
- `messages` (array, required): Conversation history
- `temperature` (float, optional): Sampling temperature (0.0-2.0)
- `max_tokens` (int, optional): Maximum completion tokens
- `de2generator` (object, optional): Custom de²generator settings
  - `model_endpoint` (string): Override vLLM backend (uses config.json if omitted)
  - `agent_name` (string): Caller identifier (for telemetry)
  - `goal_id` (string): Goal identifier (for telemetry)
  - `cancellation_token` (string): Unique client-generated token used to cancel this request

**Response:**
```json
{
  "id": "chatcmpl-8f7a9c2e",
  "object": "chat.completion",
  "created": 1721654321,
  "model": "Qwen/Qwen3.5-35B-A3B-FP8",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "2 + 2 = 4",
        "reasoning_content": "This is a simple arithmetic question..."
      },
      "finish_reason": "stop"
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 42,
    "total_tokens": 67
  },
  "de2generator": {
    "request_id": "068dfb46-67c2-462f-a703-1e63618fedc6",
    "model": "Qwen/Qwen3.5-35B-A3B-FP8",
    "generation_attempts": 1,
    "recovery_count": 0,
    "checkpoints_created": 2,
    "checkpoints_rejected": 0,
    "total_tokens_generated": 42,
    "thinking_tokens": 150,
    "min_entropy": 6.2,
    "max_entropy": 6.8,
    "avg_entropy": 6.5,
    "semantic_verifier_enabled": true,
    "semantic_verifier_calls": 2,
    "semantic_verifier_failures": 0,
    "log_file_path": "log_llm_interaction_20240721_143254_123_0001.txt"
  }
}
```

**Response Fields:**

Standard OpenAI fields:
- `id`: Unique completion ID
- `created`: Unix timestamp
- `choices`: Array with message and finish reason
- `usage`: Token counts

de²generator fields (in `de2generator` object):
- `request_id`: Unique session ID for this request
- `generation_attempts`: Number of retries performed
- `recovery_count`: Number of successful rollbacks
- `checkpoints_created`: Total checkpoints created
- `checkpoints_rejected`: Checkpoints escalated to REJECTED state
- `total_tokens_generated`: Output token count
- `thinking_tokens`: Tokens used in reasoning (extended thinking models)
- `min_entropy` / `max_entropy` / `avg_entropy`: Shannon entropy statistics
- `semantic_verifier_enabled`: Whether semantic validation ran
- `semantic_verifier_calls`: Number of semantic checks performed
- `semantic_verifier_failures`: Failed semantic verification requests
- `log_file_path`: Path to telemetry log (if thinking exhaustion occurred)

**Errors:**

```json
{
  "error": {
    "message": "Cannot connect to vLLM backend",
    "type": "connection_error",
    "code": 502
  }
}
```

| Status | Code | Description |
|--------|------|-------------|
| 200 | OK | Generation successful |
| 400 | Bad Request | Invalid request format |
| 409 | Conflict | Cancellation token is already used by an active request |
| 499 | Client Closed Request | Generation was cancelled through `/v1/cancel` |
| 502 | Bad Gateway | vLLM backend unreachable |
| 500 | Internal Server Error | Unexpected error in de²generator |

---

### `POST /v1/cancel`

Cancels an active chat completion identified by its client-supplied cancellation token.
The cancellation request returns 200, while the interrupted completion request returns 499.

**Request:**
```json
{
  "cancellation_token": "unique-client-generated-token"
}
```

**Response:**
```json
{
  "status": "cancelled",
  "request_id": "068dfb46-67c2-462f-a703-1e63618fedc6"
}
```

`status` is `cancelled`, `already_cancelled`, or `not_found`. Repeated and unknown
cancellation requests are idempotent and return HTTP 200.

---

### `GET /health`

**Liveness** probe. Does not touch the backend, the semantic verifier, or Redis.

```json
{ "status": "ok", "component": "de2generator" }
```

Always `200` while the process can serve. The Docker `HEALTHCHECK` uses exactly this
endpoint: a container is marked unhealthy only when this stops answering, because an
orchestrator can restart a dead proxy but cannot fix a dead upstream. Backend state is
reported by `/health/upstream` instead — see below.

---

### `GET /health/upstream`

**Diagnostic** (never gating): asks the configured backend once whether it is there.

```json
{
  "base_url": "http://127.0.0.1:8081",
  "reachable": true,
  "status_code": 401,
  "error": null
}
```

Or, when nothing is listening:

```json
{
  "base_url": "http://127.0.0.1:1",
  "reachable": false,
  "status_code": null,
  "error": "ConnectError: All connection attempts failed"
}
```

- `base_url` is the *resolved* upstream (`VLLM_BASE_URL` env wins over `config.json`), so the
  answer is always about the URL actually in use — `base_url = "http://localhost:8000/v1"`
  in these docs predates the env override.
- **Any HTTP status counts as reachable.** `401`/`403` mean "present, credentials required"
  — expected against an authenticated gateway — and are *not* failures. Only a transport
  error (connection refused, DNS, timeout) sets `reachable: false`.
- `status_code` / `error` are mutually exclusive; the probe itself always returns `200`, so a
  red upstream can never break a monitoring check.
- Probes `GET {base_url}/v1/models` with a 4-second budget. This endpoint is what to alert
  on; the container healthcheck deliberately ignores it.

---

### `GET /ui`

Serves the web UI HTML.

**Response:** HTML document

**Status:** 200 OK

---

### `GET /ui/app.js`

Serves the main JavaScript entry point (ES6 module).

**Response:** JavaScript (type: `application/javascript`)

**Status:** 200 OK

---

### `GET /ui/scripts/{path}`

Serves JavaScript modules and other static assets.

**Path Parameters:**
- `{path}`: Relative path to module or asset (e.g., `config.js`, `utils/theme.js`, `rendering/card.js`)

**Response:** JavaScript or CSS file

**Status:** 
- 200 OK — File found
- 404 Not Found — File doesn't exist
- 403 Forbidden — Path contains `..` (directory traversal prevention)

**Examples:**
- `/ui/scripts/config.js` → Returns config.js
- `/ui/scripts/utils/dom.js` → Returns utils/dom.js
- `/ui/scripts/events/handlers.js` → Returns events/handlers.js

---

### `GET /ui/style.css`

Serves the main stylesheet.

**Response:** CSS (type: `text/css`)

**Status:** 200 OK

---

### `GET /ui/config.json`

Serves frontend configuration (read-only subset).

**Response:**
```json
{
  "model_endpoint": "http://localhost:8000/v1/chat/completions",
  "semantic_llm": {
    "enabled": true,
    "endpoint": "http://localhost:8350/v1/chat/completions"
  },
  "detection": {
    "entropy": {
      "min_healthy": 3.5,
      "max_healthy": 7.0
    }
  },
  "ui": {
    "max_history": 50,
    "thinking_chars_per_token": 4
  }
}
```

**Status:** 200 OK or 500 if config loading fails

---

### `GET /ui/events`

Server-Sent Events (SSE) stream for real-time UI updates.

**Connection:** Persistent, client auto-reconnects on disconnect

**Response Format:** Server-Sent Events (line-delimited JSON)

```
data: {"type": "history", "data": [...]}

data: {"type": "interaction_start", "data": {"request_id": "...", "model": "..."}}

data: {"type": "checkpoint", "data": {"checkpoint_id": 0, "tokens": 5, ...}}

data: {"type": "stats_update", "data": {"tps": 2.5, "entropy": 6.5}}

data: {"type": "interaction_complete", "data": {"request_id": "...", "status": "ok"}}
```

**Event Types:**

| Type | Data |
|------|------|
| `history` | Array of past interactions (on connect) |
| `interaction_start` | New generation started |
| `checkpoint` | Checkpoint created or updated |
| `stats_update` | Token metrics updated |
| `relabel_last_checkpoint` | Checkpoint state changed |
| `checkpoint_semantic_update` | Semantic validation result |
| `interaction_complete` | Generation finished |

**Status:** 200 OK (stream continues)

---

## Response Objects

### Checkpoint Object

```json
{
  "id": "cp-068dfb46-0",
  "checkpoint_id": 0,
  "token_position": 42,
  "content": "The capital of France is Paris.",
  "entropy": 6.5,
  "entropy_status": "ok",
  "semantic_results": {
    "character_repetition": false,
    "word_repetition": false,
    "phrase_repetition": false,
    "semantic_stagnation": false,
    "poor_structure": false,
    "incoherence": false,
    "failing_count": 0
  },
  "status": "ok",
  "metrics": {
    "tokens": 5,
    "thinking_tokens": 0,
    "tps": 2.4,
    "elapsed_seconds": 2.1
  }
}
```

**Fields:**
- `id`: Unique checkpoint identifier
- `token_position`: Position in token stream
- `content`: Generated text at this checkpoint
- `entropy`: Shannon entropy score
- `entropy_status`: "ok" | "candidate_reject" | "rejected"
- `semantic_results`: Results from semantic verifier (6 boolean categories)
- `status`: Overall checkpoint status
- `metrics`: Token and timing metrics

### Interaction Object

```json
{
  "request_id": "068dfb46-67c2-462f-a703-1e63618fedc6",
  "model": "Qwen/Qwen3.5-35B-A3B-FP8",
  "status": "complete",
  "message": "2 + 2 = 4",
  "thinking": "Let me think about this simple arithmetic...",
  "stats": {
    "total_tokens": 42,
    "thinking_tokens": 0,
    "generation_attempts": 1,
    "recovery_count": 0,
    "checkpoints_created": 2,
    "checkpoints_rejected": 0,
    "avg_entropy": 6.5,
    "avg_tps": 2.4,
    "elapsed_seconds": 17.5
  },
  "checkpoints": [
    { ...checkpoint object... },
    { ...checkpoint object... }
  ]
}
```

---

## Error Responses

### Connection Error

```json
{
  "error": {
    "message": "Cannot reach vLLM at http://localhost:8000",
    "type": "connection_error"
  }
}
```

**HTTP Status:** 502 Bad Gateway

### Semantic Verifier Failure (Graceful Degradation)

de²generator continues with entropy detection only:

```json
{
  "de2generator": {
    "semantic_verifier_enabled": false,
    "semantic_verifier_failures": 3,
    "warning": "Semantic verifier unavailable, using entropy detection only"
  }
}
```

**HTTP Status:** 200 OK (generation still succeeds)

### Request Validation Error

```json
{
  "error": {
    "message": "Missing required field: 'messages'",
    "type": "validation_error"
  }
}
```

**HTTP Status:** 400 Bad Request

### Timeout Error

```json
{
  "error": {
    "message": "Request timeout after 300 seconds",
    "type": "timeout"
  }
}
```

**HTTP Status:** 504 Gateway Timeout

---

## Rate Limiting

No explicit rate limiting in de²generator. Rate limiting should be applied at proxy layer or vLLM backend.

---

## Best Practices

1. **Always include error handling** for HTTP status codes 500, 502, 504
2. **Implement exponential backoff** for SSE reconnection
3. **Use `request_id`** from response for telemetry correlation
4. **Monitor `semantic_verifier_failures`** to detect configuration issues
5. **Parse `de2generator` metadata** for insights into generation quality
6. **Cache `/ui/config.json`** (refresh on 5xx errors)

---

## Examples

### Python Client (with error handling)

```python
import httpx
import json

async def query_de2generator(prompt: str) -> dict:
    async with httpx.AsyncClient(timeout=300) as client:
        try:
            response = await client.post(
                "http://localhost:8356/v1/chat/completions",
                json={
                    "model": "Qwen/Qwen3.5-35B",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.7
                }
            )
            response.raise_for_status()
            
            data = response.json()
            message = data["choices"][0]["message"]["content"]
            stats = data.get("de2generator", {})
            
            print(f"Answer: {message}")
            print(f"Attempts: {stats.get('generation_attempts')}")
            print(f"Recovery: {stats.get('recovery_count')}")
            print(f"Avg Entropy: {stats.get('avg_entropy')}")
            
            return data
            
        except httpx.ConnectError:
            print("Error: Cannot connect to de²generator")
        except httpx.HTTPStatusError as e:
            print(f"Error: {e.response.status_code} {e.response.json()}")
```

### JavaScript SSE Client

```javascript
function connectToEvents() {
  const eventSource = new EventSource('/ui/events');
  
  eventSource.addEventListener('checkpoint', (event) => {
    const checkpoint = JSON.parse(event.data).data;
    console.log(`Checkpoint: entropy=${checkpoint.entropy}, status=${checkpoint.status}`);
    updateUI(checkpoint);
  });
  
  eventSource.addEventListener('interaction_complete', (event) => {
    console.log('Generation complete');
    const interaction = JSON.parse(event.data).data;
    displayResult(interaction);
  });
  
  eventSource.onerror = () => {
    console.log('SSE connection lost, reconnecting...');
    setTimeout(connectToEvents, 5000);  // Exponential backoff
  };
}
```

### cURL Examples

**Basic generation:**
```bash
curl -X POST http://localhost:8356/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3.5-35B",
    "messages": [{"role": "user", "content": "Hello"}],
    "temperature": 0.7
  }'
```

**With custom endpoint:**
```bash
curl -X POST http://localhost:8356/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen/Qwen3.5-35B",
    "messages": [{"role": "user", "content": "Hello"}],
    "de2generator": {
      "model_endpoint": "http://my-vllm:8000/v1/chat/completions"
    }
  }'
```

**Stream SSE events:**
```bash
curl -N http://localhost:8356/ui/events
```

---

## Versioning

Current API version: **1.0**

OpenAI compatibility: **v1** (matching `/v1/chat/completions`)

No breaking changes planned in this version. Future versions will use `/v2/...` for incompatible changes.
