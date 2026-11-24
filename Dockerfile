FROM python:3.13-alpine

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files (NOTE: config.json is mounted as volume, not baked into image)
COPY src/ src/

# Expose the default port
EXPOSE 8356

# Set environment variables with defaults
ENV DE2_HOST=0.0.0.0
ENV DE2_PORT=8356
# Same default as docker-compose.yml. Override it wherever the backend actually
# lives: this image default only works for a host-networked container on a machine
# running the LiteLLM gateway on :8081.
ENV VLLM_BASE_URL=http://127.0.0.1:8081

# Liveness only, in agreement with docker-compose.yml. /v1/models is *proxied*, so a
# non-200 there describes the backend — and restarting this container cannot fix a
# backend; it also can never pass against an authenticated gateway. Backend state
# belongs to GET /health/upstream. No curl in alpine: use httpx (already a dep).
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD /bin/sh -c "python -c \"import httpx,sys; sys.exit(0 if httpx.get('http://localhost:${DE2_PORT}/health', timeout=5).status_code == 200 else 1)\"" || exit 1

# Run the application
CMD ["python", "src/de2generator.py"]
