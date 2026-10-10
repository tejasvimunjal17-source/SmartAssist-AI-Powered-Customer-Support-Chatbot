# SmartAssist - Railway-ready image.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TOKENIZERS_PARALLELISM=false

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# CPU-only PyTorch first: the default wheel bundles CUDA libraries (GBs) that
# a CPU-only Railway container can never use. Smaller image = faster
# deploys and faster cold starts.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

# Fail the build (instead of shipping a broken deploy) if app/ files come from
# different revisions, e.g. the orchestrator reading GeneratedResponse.fallback_reason
# that an older response_generator.py doesn't define.
RUN python -m scripts.check_response_contract

# Bake the embedding model and the vector index into the image so neither is
# downloaded/built while a customer is waiting. Non-fatal on purpose: if this
# step fails, the app still starts and rebuilds the index in the background
# (see app/main.py warm-up).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')" \
    && python -m scripts.build_index \
    || echo "WARNING: model/index pre-build failed; will be done at startup instead"

EXPOSE 8000

# Railway injects $PORT; fall back to 8000 for local Docker. Shell form is
# required so ${PORT} is expanded. One worker: the embedding model lives in
# memory, and the duplicate-request guard is per-process.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen('http://localhost:%s/' % os.environ.get('PORT','8000'), timeout=3)" || exit 1

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]