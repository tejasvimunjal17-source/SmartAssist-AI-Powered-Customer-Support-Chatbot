# Day 15 — Docker Verification Notes

## Status: NOT built or run. Honest account below.

Docker is not installed in the sandbox this project was built in (same
no-internet-access sandbox documented throughout Days 1–15). Running
`docker --version` there returns `command not found`. This means:

- `docker build` was **never executed**.
- `docker run` / `docker compose up` was **never executed**.
- No container has ever actually served this application.
- No image was ever pushed anywhere.

Nothing below claims otherwise.

## What WAS actually checked

| Check | Method | Result |
|---|---|---|
| `docker-compose.yml` is valid YAML | Parsed with Python's `yaml.safe_load()` | **Passed** — parses to the expected structure (service, ports, env_file, volume) |
| `Dockerfile` references the correct app entry point | Manually verified `app/main.py` defines `app = FastAPI(...)` and the Dockerfile's `CMD` targets `app.main:app` | Matches |
| `requirements.txt` is what gets installed | Read directly; `Dockerfile`'s `COPY requirements.txt .` + `pip install -r requirements.txt` references it exactly | Matches |
| spaCy model name | `app/preprocessing.py` uses `en_core_web_sm`; Dockerfile downloads exactly that | Matches |
| `.dockerignore` excludes real secrets/runtime data | Manually reviewed against the same exclusion list used in `.gitignore` (`.env`, `*.db`, `chroma_db/`, `.cache/`, `venv/`, `__pycache__/`) | Covers the same categories |
| Dockerfile syntax | No `hadolint` or similar linter is installed in this sandbox; reviewed line by line by hand instead | No linter run — manual review only |

## What was NOT checked (and why)

- **Whether `pip install -r requirements.txt` actually succeeds inside
  the `python:3.11-slim` image.** `sentence-transformers` and `chromadb`
  pull in several transitive dependencies (torch, onnxruntime, etc.)
  that sometimes have platform-specific wheel availability issues. This
  is the single most likely thing to need a fix on first real build —
  see "What to check first" below.
- **Whether the container actually starts and serves `/chat`, `/history`,
  `/feedback`, `/app/`, and the admin routes.** No way to make an HTTP
  request to a non-existent container.
- **Image size, build time, or layer caching behavior.**
- **The `HEALTHCHECK` command's actual behavior** (it's a standard
  pattern — `urllib.request.urlopen` against `/` — but was never run).

## A known, deliberate limitation: SQLite persistence

`conversations.db` and `admin.db` (Day 8 / Day 11) are created as single
files at the project root (`app/config.py`'s `BASE_DIR`, which is `/app`
inside the container) — not inside a directory. `chroma_db/` (Day 4) *is*
a directory, so `docker-compose.yml` mounts a named volume over it
cleanly. The two SQLite files are not given the same treatment, **on
purpose**: making them persist properly means either (a) mounting a
volume at `/app` itself, which would also shadow the application code
with whatever's in the volume, or (b) changing `app/config.py` to read
`CONVERSATION_DB_PATH`/`ADMIN_DB_PATH` from environment variables so a
volume can target a dedicated `/data` directory instead. Option (b) is
the right fix, but it's an application-code change beyond "add Docker
files" — left undone rather than rushed in alongside Docker work that
was explicitly scoped to not touch the already-verified (459/459
passing) application code unnecessarily.

**Practical effect today:** every time the container is removed and
recreated (not just restarted), conversation history, feedback, and
admin audit logs reset to empty. The knowledge base articles themselves
are unaffected (they ship inside the image, not in a database).

## What to check first when you actually build this

1. `docker build -t smartassist .` — watch for any wheel build failures
   around `sentence-transformers`/`chromadb`'s dependencies; if one
   fails, it's almost certainly a missing system library, fixable by
   adding it to the `apt-get install` line.
2. `docker run --rm -p 8000:8000 --env-file .env smartassist` (after
   copying `.env.example` to `.env` and filling in a real
   `GEMINI_API_KEY` and `ADMIN_PASSWORD_HASH`) — confirm `GET /` returns
   `{"status": "SmartAssist is running"}`.
3. Visit `http://localhost:8000/app/` for the customer chat UI and
   `http://localhost:8000/app/admin.html` for the admin panel.
4. Run `docker exec <container> python -m scripts.build_index` to build
   the real ChromaDB index inside the running container (needs the real
   sentence-transformers model, which downloads on first use).
