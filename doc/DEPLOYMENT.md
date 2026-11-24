# Deployment Guide

## Overview

de²generator is deployed as a containerized service between Thalabus and the LLM backend. This guide covers local development, Docker deployment, Kubernetes, and production best practices.

## Verified Deployment (thalabus fleet)

> Verified with the operator on 2026-11-14, cross-checked against `config.json` and
> `docker-compose.yml`. This section is the **real** deployment; the sections below it are
> generic/illustrative and several of their environment-variable names are not read by the
> code (see the correction table).

- **Runs on:** `thalabus-ai-1`.
- **Who uses it:** every Thalabus instance — including debug instances on laptops and the
  demonstration instances on `thalabus-ai-1` and `thalabus-ai-2` — reaches the LLM gateway
  (LiteLLM / gateway-llm-lite) **through de²generator** rather than directly. It is on the
  hot path, not an optional extra, despite what the compose header implies.
- **Upstream:** `vllm.base_url` in `config.json` is `http://localhost:8081`, i.e. the
  LiteLLM gateway on the AI subnet (the same base URL the hub uses). Under Compose,
  `docker-compose.yml` sets `VLLM_BASE_URL` as an environment default, and env **wins over**
  config — so a plain `docker compose up` targets `http://127.0.0.1:8081` (the gateway on
  this host). The previous default, `http://host.docker.internal:8355`, still ships as the
  documented override but **never resolved on this Linux box**: `host.docker.internal` is a
  Docker Desktop feature, so that value was dead code and caused the container to report
  `unhealthy` on every probe. Export `VLLM_BASE_URL` to point elsewhere.
- **Side models:** the semantic verifier (`:8350`) and translator (`:8351`) come from the
  workspace's `llms/` launchers (`llms/qwen.sh 3.5-2B-FP8`, `llms/hy.sh`).

### Name corrections

Several examples below predate the current code and use names that are **never read**. Real
names live in `src/core/constants.py` (full list in the Environment Variables section below):

| Used below (wrong) | Real name |
|--------------------|-----------|
| `VLLM_ENDPOINT` | `VLLM_BASE_URL` (a base URL — paths are appended, don't include `/v1/chat/completions`) |
| `SEMANTIC_ENDPOINT` / `SEMANTIC_ENABLED` | `SEMANTIC_VERIFIER_ENDPOINT` / `SEMANTIC_LLM_ENABLED` |
| `DE2GENERATOR_HOST` / `DE2GENERATOR_PORT` | `DE2_HOST` / `DE2_PORT` |
| `DE2GENERATOR_CONFIG` | not supported — the path is fixed at `<project root>/config.json` |
| `LOG_LEVEL` | not supported — no verbosity knob exists |

Those blocks were left as-is rather than rewritten (several are aspirational Kubernetes
samples); apply this table when copying them.

## Local Development

### Prerequisites

- Python 3.13+
- vLLM running locally (port 8000 by default)
- (Optional) Semantic verifier LLM

### Setup

```bash
# Clone and initialize
git clone <repo>
cd de2generator
python -m venv .venv
.venv\Scripts\activate  # or source .venv/bin/activate on Linux/macOS

# Install dependencies
pip install -r requirements.txt

# Configure
cp config.example.json config.json
# Edit config.json with local vLLM endpoints

# Run
python src/de2generator.py
```

**Output:**
```
de²generator started on http://0.0.0.0:8356
  └─ Web UI: http://localhost:8356/ui
  └─ API: http://localhost:8356/v1/chat/completions
  └─ SSE Events: http://localhost:8356/ui/events
```

Access UI at **http://localhost:8356/ui**

### Environment Variables

These override `config.json` values. Only these exist (verified against
`src/core/constants.py` 2026-11-14) — detector, recovery and history knobs have **no**
environment override and must be set in `config.json`:

| Variable | Config key | Purpose |
|----------|-----------|---------|
| `DE2_HOST` | `server.host` | Server bind address |
| `DE2_PORT` | `server.port` | Server port |
| `DE2_PROTOCOL` | `server.protocol` | `http` / `https` |
| `DE2_SSL_CERT` / `DE2_SSL_KEY` | `server.ssl_cert_path` / `.ssl_key_path` | HTTPS material |
| `VLLM_BASE_URL` | `vllm.base_url` | Primary LLM backend (base URL, not a full completions URL) |
| `SEMANTIC_LLM_ENABLED` | `semantic_llm.enabled` | Enable semantic verifier |
| `SEMANTIC_VERIFIER_ENDPOINT` | `semantic_llm.endpoint` | Semantic verifier address |
| `SEMANTIC_VERIFIER_MODEL` / `SEMANTIC_VERIFIER_TIMEOUT` | `semantic_llm.model` / `.timeout_seconds` | Verifier model / budget |
| `SEMANTIC_MAX_MODEL_LEN` | `semantic_llm.max_model_len` | Verifier context window guard |
| `DE2_UI_ENABLED` / `DE2_UI_SHOW_CONTENT` | `ui.enabled` / `ui.show_content` | Dashboard toggles |
| `TRANSLATION_ENABLED` / `TRANSLATION_ENDPOINT` / `TRANSLATION_MODEL` / `TRANSLATION_TIMEOUT` / `TRANSLATION_FORCE_LANG` | `translation.*` | Optional answer translation |

There is no `LOG_LEVEL`: the app logs to stdout at uvicorn `info` level with access logs on.

**Example:**
```bash
export VLLM_BASE_URL=http://gpu-server:8000
export DE2_PORT=9000
python src/de2generator.py
```

---

## Docker Deployment

### Building the Image

```bash
# Build locally
docker build -t de2generator:latest .

# Tag for registry
docker tag de2generator:latest your-registry/de2generator:latest
docker push your-registry/de2generator:latest
```

### Container Structure

```
FROM python:3.13-alpine (SECURITY: 3.13 is latest + smallest attack surface)

COPY src/           /app/src/
COPY requirements.txt /app/
RUN pip install -r requirements.txt

EXPOSE 8356
CMD ["python", "src/de2generator.py"]
```

### Running Container

**Basic:**
```bash
docker run -p 8356:8356 \
  -v $(pwd)/config.json:/app/config.json \
  de2generator:latest
```

**With environment variables:**
```bash
docker run -p 8356:8356 \
  -v $(pwd)/config.json:/app/config.json \
  -e VLLM_BASE_URL=http://gpu-server:8000 \
  de2generator:latest
```

**With resource limits:**
```bash
docker run -p 8356:8356 \
  -v $(pwd)/config.json:/app/config.json \
  --memory=2g \
  --cpus=2 \
  de2generator:latest
```

### Health Check

Shipped in both `Dockerfile` and `docker-compose.yml` (revised 2026-11-18 — see the
rationale below, which reverses the 2026-11-14 version of this section):

```yaml
healthcheck:
  test: ["CMD", "/bin/sh", "-c", "python -c \"import httpx,sys; sys.exit(0 if httpx.get('http://localhost:8356/health', timeout=5).status_code == 200 else 1)\""]
  interval: 30s
  timeout: 10s
  retries: 3
  start_period: 10s
```

Why it looks like this:

- **`/health`, i.e. liveness only.** The earlier probe used the proxied `/v1/models` so
  that "healthy" would mean "the backend is reachable too". That was the wrong question in
  this deployment, for two reasons: an orchestrator can restart a dead proxy but cannot fix
  a dead backend, so tying the badge to the backend turns a clean `502` into an outage; and
  the upstream is an authenticated gateway that answers `401` to an unauthenticated
  `/v1/models`, so the probe could *never* pass — a permanently red badge tells you nothing.
  Backend state moved to `GET /health/upstream`, which never gates anything.
- **Any HTTP status counts as reachable.** `reachable: true, status_code: 401` means "the
  upstream is there and wants a key", which is a configuration finding, not an outage.
  Confusing the two is what made the badge useless.
- **httpx, not curl.** The image is `python:3.13-alpine`, which has no `curl`; the earlier
  `curl -f …/ui` examples in this file could never pass.
- **Alert on the diagnostic, not the badge.** Prometheus/Synapse should watch
  `/health/upstream` (`reachable`, and `status_code` in `{401,403}` meaning "key needed")
  while the container badge stays reserved for "the process is serving".

---

## Docker Compose

Complete development/staging setup:

```yaml
version: '3.8'

services:
  de2generator:
    image: de2generator:latest
    ports:
      - "8356:8356"
    environment:
      - VLLM_ENDPOINT=http://vllm:8000/v1/chat/completions
      - SEMANTIC_ENDPOINT=http://semantic:8350/v1/chat/completions
      - LOG_LEVEL=INFO
    volumes:
      - ./config.json:/app/config.json
      - ./logs:/app/logs
    depends_on:
      - vllm
      - semantic
    healthcheck:
      test: ["CMD", "/bin/sh", "-c", "python -c \"import httpx,sys; sys.exit(0 if httpx.get('http://localhost:8356/v1/models', timeout=8).status_code == 200 else 1)\""]
      interval: 30s
      timeout: 15s
      retries: 3

  vllm:
    image: vllm/vllm-openai:latest
    ports:
      - "8000:8000"
    environment:
      - MODEL_NAME=Qwen/Qwen3.5-35B
      - CUDA_VISIBLE_DEVICES=0
    volumes:
      - /mnt/models:/root/.cache/huggingface/hub
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]

  semantic:
    image: vllm/vllm-openai:latest
    ports:
      - "8350:8000"
    environment:
      - MODEL_NAME=Qwen/Qwen2.5-3B-Instruct
      - CUDA_VISIBLE_DEVICES=1
    volumes:
      - /mnt/models:/root/.cache/huggingface/hub
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
```

**Start:**
```bash
docker-compose up -d
docker-compose logs -f de2generator
```

**Stop:**
```bash
docker-compose down
```

---

## Kubernetes Deployment

### Deployment Manifest

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: de2generator
  namespace: default
spec:
  replicas: 2
  selector:
    matchLabels:
      app: de2generator
  template:
    metadata:
      labels:
        app: de2generator
    spec:
      containers:
      - name: de2generator
        image: your-registry/de2generator:latest
        imagePullPolicy: Always
        ports:
        - name: http
          containerPort: 8356
        env:
        - name: VLLM_ENDPOINT
          value: "http://vllm-service:8000/v1/chat/completions"
        - name: SEMANTIC_ENDPOINT
          value: "http://semantic-service:8350/v1/chat/completions"
        - name: LOG_LEVEL
          value: "INFO"
        volumeMounts:
        - name: config
          mountPath: /app/config.json
          subPath: config.json
        resources:
          requests:
            memory: "1Gi"
            cpu: "1000m"
          limits:
            memory: "2Gi"
            cpu: "2000m"
        livenessProbe:
          httpGet:
            path: /ui
            port: http
          initialDelaySeconds: 10
          periodSeconds: 30
          timeoutSeconds: 5
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /ui
            port: http
          initialDelaySeconds: 5
          periodSeconds: 10
          timeoutSeconds: 3
          failureThreshold: 2
      volumes:
      - name: config
        configMap:
          name: de2generator-config

---
apiVersion: v1
kind: Service
metadata:
  name: de2generator
  namespace: default
spec:
  selector:
    app: de2generator
  ports:
  - name: http
    port: 8356
    targetPort: http
  type: ClusterIP

---
apiVersion: v1
kind: ConfigMap
metadata:
  name: de2generator-config
  namespace: default
data:
  config.json: |
    {
      "model_endpoint": "http://vllm-service:8000/v1/chat/completions",
      "semantic_llm": {
        "enabled": true,
        "endpoint": "http://semantic-service:8350/v1/chat/completions",
        "model": "Qwen/Qwen2.5-3B-Instruct"
      },
      "detection": {
        "entropy": {
          "min_healthy": 3.5,
          "max_healthy": 7.0,
          "min_block_chars": 100
        }
      },
      "recovery": {
        "max_attempts": 3,
        "checkpoint_interval_tokens": 250
      }
    }
```

**Deploy:**
```bash
kubectl apply -f deployment.yaml
kubectl get pods -l app=de2generator
kubectl logs -f deployment/de2generator
```

### Service Discovery

For Thalabus → de²generator connectivity:

```yaml
# In Thalabus deployment config
DE2GENERATOR_ENDPOINT: http://de2generator.default.svc.cluster.local:8356/v1/chat/completions
```

### Scaling

```bash
# Scale to 3 replicas
kubectl scale deployment de2generator --replicas=3

# Auto-scale (requires metrics server)
kubectl autoscale deployment de2generator --min=2 --max=10 --cpu-percent=80
```

---

## Configuration Files

### config.json Template

```json
{
  "model_endpoint": "http://localhost:8000/v1/chat/completions",
  "semantic_llm": {
    "enabled": true,
    "endpoint": "http://localhost:8350/v1/chat/completions",
    "model": "Qwen/Qwen2.5-3B-Instruct",
    "timeout_seconds": 10,
    "max_retries": 2
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
    "checkpoint_interval_tokens": 250
  },
  "ui": {
    "max_history": 50,
    "thinking_chars_per_token": 4
  }
}
```

### Secrets Management (Kubernetes)

Store sensitive config in secrets:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: de2generator-secrets
type: Opaque
stringData:
  vllm-endpoint: http://internal-vllm:8000/v1/chat/completions
  semantic-endpoint: http://internal-semantic:8350/v1/chat/completions
  api-key: your-api-key-here
```

Reference in deployment:

```yaml
env:
- name: VLLM_ENDPOINT
  valueFrom:
    secretKeyRef:
      name: de2generator-secrets
      key: vllm-endpoint
```

---

## Monitoring & Logging

### Logs

**Docker:**
```bash
docker logs -f <container-id>
```

**Kubernetes:**
```bash
kubectl logs -f deployment/de2generator
```

**Log Levels:**
- `DEBUG`: All requests, detected anomalies, recovery steps
- `INFO`: Started, errors, recovery summaries (default)
- `WARNING`: Semantic verifier failures, timeouts
- `ERROR`: Critical failures

**Set log level:**
```bash
export LOG_LEVEL=DEBUG
python src/de2generator.py
```

### Telemetry Files

de²generator writes telemetry to `log_llm_interaction_YYYYMMDD_HHMMSS_mmm_XXXX.txt` files when thinking exhaustion occurs.

**Mount for persistence:**
```bash
docker run -v /persistent-logs:/app/logs de2generator:latest
```

### Metrics

de²generator provides response metadata for monitoring:

```python
# From response metadata
metrics = response["de2generator"]
print(f"Generation attempts: {metrics['generation_attempts']}")
print(f"Recovery count: {metrics['recovery_count']}")
print(f"Avg entropy: {metrics['avg_entropy']}")
print(f"Min entropy: {metrics['min_entropy']}")
```

**Key metrics to track:**
- `generation_attempts` — Should stay at 1 (no recovery needed)
- `recovery_count` — Track if increasing (model degradation?)
- `avg_entropy` — Should be 5.0-6.5 (model health indicator)
- `semantic_verifier_failures` — Track connectivity issues

### Prometheus Integration (Future)

Export metrics endpoint (not yet implemented):
```
GET /metrics

# de2generator_generation_attempts_total
# de2generator_recovery_count_total
# de2generator_checkpoint_entropy_histogram
```

---

## Performance Tuning

### Detection Speed vs Accuracy

**Faster (less detection):**
```json
{
  "detection": {
    "entropy": {
      "min_block_chars": 200  # Larger blocks = fewer checks
    },
    "semantic_llm": {
      "enabled": false  # Disable async validation
    }
  }
}
```

**Slower but more accurate:**
```json
{
  "detection": {
    "entropy": {
      "min_block_chars": 50  # Smaller blocks = more checks
    },
    "semantic_llm": {
      "enabled": true  # Full semantic validation
    }
  }
}
```

### Memory Management

**Monitor checkpoint memory:**
- Each checkpoint stores ~1-10KB
- High token throughput = more checkpoints
- Example: 50 tokens/sec × 10KB per checkpoint = 500KB/sec

**Limit history in UI (config.json):**
```json
{
  "ui": {
    "max_history": 50  # Only keep last 50 interactions
  }
}
```

### Concurrent Sessions

Deploy multiple replicas for load distribution:

```bash
# Docker Compose
docker-compose up -d --scale de2generator=3

# Kubernetes
kubectl scale deployment de2generator --replicas=3
```

---

## Troubleshooting

| Issue | Diagnosis | Solution |
|-------|-----------|----------|
| UI shows "disconnected" | Check server logs, network connectivity | Verify port 8356 is open; check firewall |
| Semantic verifier errors | Check semantic LLM logs | Verify endpoint in config.json; restart semantic service |
| High memory usage | Monitor checkpoint accumulation | Reduce checkpoint interval or enable cleanup |
| Slow generation | Check vLLM latency | Monitor vLLM GPU utilization; add more GPUs |
| Config not loading | Check file permissions | Verify config.json exists and is readable |

### Debug Mode

```bash
export LOG_LEVEL=DEBUG
python src/de2generator.py
```

**Expect verbose output:**
```
DEBUG: Request incoming: model=Qwen3.5
DEBUG: Streaming from vLLM...
DEBUG: Checkpoint 0: entropy=6.5, status=ok
DEBUG: Checkpoint 1: entropy=3.2, status=candidate_reject (strike 1)
DEBUG: Recovery attempt 1/3...
```

### Port Conflicts

If port 8356 is already in use:

```bash
# Find process using port
lsof -i :8356  # macOS/Linux
netstat -ano | findstr :8356  # Windows

# Use different port
export DE2GENERATOR_PORT=9000
python src/de2generator.py
```

---

## Production Checklist

- [ ] Use `python:3.13-alpine` base image (not -slim or -full)
- [ ] Set `LOG_LEVEL=INFO` (not DEBUG)
- [ ] Liveness health check configured (`/health`), backend state monitored separately
      (`/health/upstream`)
- [ ] Persist `logs/` (bind mount or volume): interaction logs and
      `hourly_token_stats.json` live in the container's writable layer otherwise and are
      destroyed by every recreate
- [ ] Set resource limits (memory, CPU)
- [ ] Use environment variables for sensitive config (not config.json)
- [ ] Enable semantic verifier for production quality
- [ ] Monitor `avg_entropy` and `recovery_count` metrics
- [ ] Set up log aggregation (ELK, Splunk, etc.)
- [ ] Document runbook for common issues
- [ ] Plan for vLLM or semantic LLM downtime (graceful degradation)
- [ ] Test rollback procedures
- [ ] Use GitOps for deployment versioning

---

## Security Considerations

### Network

- **Restrict access** to de²generator from only Thalabus (firewall rules)
- **Use HTTPS** if exposed beyond internal network (proxy with nginx)
- **VPC/namespace isolation** in Kubernetes

### Configuration

- **Never commit secrets** to version control
- **Use environment variables or Kubernetes secrets** for sensitive config
- **Rotate API keys** periodically if applicable
- **Audit config changes** (git history, audit logs)

### Container

- **Alpine base** (smallest attack surface)
- **No root execution** (future: USER non-root)
- **Read-only filesystem** (where possible)
- **Regular image rebuilds** for security patches

### Input Validation

de²generator validates all inputs:
- Checkpoint size limits
- Prompt length limits
- Entropy thresholds (prevent DoS)

---

## Updating

### Version Upgrade

```bash
# Pull new image
docker pull your-registry/de2generator:1.1

# Update compose
docker-compose up -d --force-recreate

# Or update Kubernetes
kubectl set image deployment/de2generator \
  de2generator=your-registry/de2generator:1.1
```

### Rollback

```bash
# Docker Compose
docker-compose down
git checkout main  # Revert to previous version
docker-compose up -d

# Kubernetes
kubectl rollout undo deployment/de2generator
```

---

## Support & Issues

Report issues with deployment to:
- GitHub Issues: [repo-url]/issues
- Internal wiki: [doc-link]
- Contact: [email]
