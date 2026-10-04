# SmartAssist — AI-Powered Customer Support Chatbot
# Day 15: Docker containerization.
#
# HONESTY NOTE: this Dockerfile was written and carefully reviewed line
# by line, but Docker itself is NOT installed in the sandbox this
# project was built in (no internet access there either), so this image
# has NOT been built or run anywhere. See reports/day15_docker_notes.md
# for exactly what was and wasn't verified, and what you should check
# the first time you build it yourself.

FROM python:3.11-slim

# Prevents Python from buffering stdout/stderr, so `docker logs` shows
# FastAPI/uvicorn output immediately instead of batching it.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# sentence-transformers/chromadb pull in packages that sometimes need a
# C/C++ build toolchain for source builds on slim images, even when a
# prebuilt wheel exists for most dependencies. Installed up front, in
# its own layer, so `pip install` below is reproducible and cached
# separately from the application code.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# Dependencies first (their own Docker layer), so editing application
# code afterward doesn't force a full reinstall on every rebuild.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m spacy download en_core_web_sm

# Now the application code itself. .dockerignore (see that file) keeps
# secrets, local databases, caches, and venv/ out of the build context
# entirely — they are never even sent to the Docker daemon, let alone
# baked into the image.
COPY . .

# Day 8/11/4 store conversations.db, admin.db, and the chroma_db/ index
# at paths relative to the project root (see app/config.py's BASE_DIR).
# Inside this image that root is /app, so those paths resolve under
# /app automatically — no extra configuration needed for the app to
# find them. See reports/day15_docker_notes.md for how to persist them
# across container restarts with a volume.

EXPOSE 8000

# A lightweight check that the FastAPI process is actually serving
# requests, not just that the container process is alive.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/', timeout=3)" || exit 1

# Secrets (GEMINI_API_KEY, ADMIN_USERNAME, ADMIN_PASSWORD_HASH) are
# supplied at `docker run`/`docker compose` time via environment
# variables or an --env-file — never baked into this image. See
# .env.example for the exact variable names.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
