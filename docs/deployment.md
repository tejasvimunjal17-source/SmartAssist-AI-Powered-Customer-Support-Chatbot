# Day 15 — Deployment Readiness

## Status: DEPLOYMENT NOT PERFORMED

This sandbox has no internet access and no Render/Railway/cloud
credentials. No deployment was attempted, and **no live URL exists**.
Anything that looks like a URL anywhere in this project is a
placeholder, never a real deployed address. The brief's own listed
deliverable ("Deployed chatbot URL") is explicitly **not satisfied** by
this submission — see the main `README.md` limitations section.

What follows is the project made *ready* to deploy, plus the exact
minimal steps to actually do it yourself.

## Why the project is deployment-ready

- A working `Dockerfile` exists (see `reports/day15_docker_notes.md` for
  its honest verification status — written and reviewed, never built).
- Configuration is environment-variable driven, not hard-coded
  (`GEMINI_API_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH` — see
  `.env.example`).
- `requirements.txt` is complete and pinned to package names (not exact
  versions — see "Known gap" below).
- The app binds to `0.0.0.0` and reads the port via the standard
  `uvicorn app.main:app --host 0.0.0.0 --port $PORT` pattern both Render
  and Railway expect.

## Known gap: unpinned dependency versions

`requirements.txt` lists package names only (`fastapi`, `sentence-transformers`,
etc.) with no version pins. This has been true since Day 1 and was never
an issue in this sandbox (nothing was ever actually installed here). For
a real deployment, an unpinned `sentence-transformers`/`chromadb`/`torch`
install risks pulling a version combination that doesn't work together
on deploy day. **Recommended before deploying:** run
`pip install -r requirements.txt` successfully in a real environment
once, then run `pip freeze > requirements.lock.txt` and deploy from the
locked file instead.

## Option A — Render

1. Push this repository to GitHub (see the main README's Git section —
   remember to `git status` and confirm `.env` is not staged, same
   reminder as every prior day).
2. In the Render dashboard: **New → Web Service**, connect the repo.
3. Environment: **Docker** (Render will use the `Dockerfile` directly —
   no build/start command needed).
4. Add environment variables in Render's dashboard (never in the repo):
   `GEMINI_API_KEY`, `ADMIN_USERNAME`, `ADMIN_PASSWORD_HASH`.
5. Render sets `$PORT` automatically; the `Dockerfile`'s `uvicorn`
   command already hard-codes `8000` — Render's Docker runtime maps its
   external port to whatever `EXPOSE`/listening port the container
   uses, so this works without changes for Render specifically. (Railway
   is different — see below.)
6. Deploy. First deploy will be slow (sentence-transformers model
   download + spaCy model download at build time).
7. Once live, run the knowledge base index build once, either as a
   Render **Shell** command (`python -m scripts.build_index`) or by
   adding it as a Render **pre-deploy command**.

## Option B — Railway

Same steps as Render, with one difference: Railway injects `$PORT` and
expects the app to actually bind to it, rather than a fixed `8000`. The
current `Dockerfile`'s `CMD` is hard-coded to port 8000, which works for
local `docker run` and for Render, but **should be changed to read
`$PORT`** before deploying to Railway specifically:
```dockerfile
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
```
This one-line change was **not** made to the shipped `Dockerfile`
because it wasn't verified (no Docker here to test the substitution
behaves correctly), and the brief lists Render/Railway as alternatives,
not both required — documenting the exact change needed is more honest
than silently guessing it works.

## What "deployment-ready" does NOT mean here

- It does not mean the Docker image has ever successfully built (see
  `reports/day15_docker_notes.md`).
- It does not mean these exact steps have been tried end-to-end by
  anyone in this project's history.
- It does not mean the SQLite persistence gap (see the Docker notes) is
  solved — on most platforms' ephemeral filesystems, conversation
  history and admin data will not survive a redeploy without additional
  work (an external Postgres/managed SQLite volume, or the env-var path
  change described in the Docker notes).
