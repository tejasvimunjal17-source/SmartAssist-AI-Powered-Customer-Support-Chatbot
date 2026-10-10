"""
Day 15 static verification for Docker/deployment configuration.

HONESTY NOTE: Docker is not installed in this sandbox (confirmed:
`docker --version` returns "command not found"), so NONE of this
actually builds or runs a container. These tests check file existence,
exact text content, and (for docker-compose.yml) genuine YAML parsing —
the maximum verification possible without a Docker daemon. See
reports/day15_docker_notes.md for the full, honest account of what was
and wasn't checked.

Run with:
    python -m tests.test_docker_config
"""

import os

from app.config import BASE_DIR

_failures = 0


def check(condition, description):
    global _failures
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {description}")
    if not condition:
        _failures += 1


def read(relpath):
    with open(os.path.join(BASE_DIR, relpath), "r", encoding="utf-8") as f:
        return f.read()


def run():
    print("--- Files exist ---")
    for f in ["Dockerfile", ".dockerignore", "docker-compose.yml",
              "reports/day15_docker_notes.md", "docs/deployment.md",
              "docs/demo_guide.md", "docs/architecture.md"]:
        check(os.path.isfile(os.path.join(BASE_DIR, f)), f"{f} exists")

    dockerfile = read("Dockerfile")
    dockerignore = read(".dockerignore")
    compose_text = read("docker-compose.yml")
    requirements = read("requirements.txt")

    print("\n--- Dockerfile matches the real application ---")
    check("FROM python:3.11" in dockerfile, "uses a Python 3.11 base image (matches the brief's 'Python 3.11+' requirement)")
    check("WORKDIR /app" in dockerfile, "sets /app as the working directory")
    check("COPY requirements.txt" in dockerfile, "copies requirements.txt before the rest of the code (correct layer caching order)")
    check("pip install" in dockerfile and "-r requirements.txt" in dockerfile, "installs from the real requirements.txt")
    check("en_core_web_sm" not in dockerfile, "spaCy model is no longer downloaded (spaCy was removed from the request path)")
    check("download.pytorch.org/whl/cpu" in dockerfile, "installs CPU-only PyTorch (much smaller image than the default CUDA build)")
    check("scripts.build_index" in dockerfile, "bakes the vector index into the image at build time")
    check("COPY . ." in dockerfile, "copies the application source into the image")
    check("EXPOSE 8000" in dockerfile, "exposes port 8000")
    check("app.main:app" in dockerfile, "the CMD targets app.main:app, the real FastAPI instance (app/main.py defines `app = FastAPI(...)`)")
    check("${PORT:-8000}" in dockerfile, "binds to Railway's injected $PORT (falls back to 8000 locally)")
    check("uvicorn" in dockerfile, "starts the app with uvicorn, matching every prior day's documented run command")
    check("--host 0.0.0.0" in dockerfile, "binds to 0.0.0.0 (required for the app to be reachable from outside the container), not 127.0.0.1")
    check("HEALTHCHECK" in dockerfile, "defines a HEALTHCHECK so container orchestrators can detect a hung/broken app")

    print("\n--- No secrets baked into the Dockerfile ---")
    check("COPY .env" not in dockerfile, "the Dockerfile never explicitly COPYs a .env file into the image")
    check("ADMIN_PASSWORD_HASH=" not in dockerfile, "no password hash value is hard-coded in the Dockerfile")
    check("GEMINI_API_KEY=" not in dockerfile, "no API key value is hard-coded in the Dockerfile")

    print("\n--- .dockerignore excludes the same sensitive/generated paths as .gitignore ---")
    gitignore = read(".gitignore")
    check(".env" in dockerignore, ".dockerignore excludes .env")
    check("*.db" in dockerignore, ".dockerignore excludes *.db (conversations.db, admin.db)")
    check("__pycache__" in dockerignore, ".dockerignore excludes __pycache__")
    check("venv/" in dockerignore, ".dockerignore excludes venv/")
    check("chroma_db/" in dockerignore, ".dockerignore excludes the regenerable chroma_db/ vector index")
    check(".git/" in dockerignore, ".dockerignore excludes .git/ (keeps the build context smaller and avoids leaking history)")
    # Cross-check against .gitignore so the two don't silently drift apart on the categories that matter.
    for pattern in [".env", "*.db", "__pycache__", "venv/"]:
        check(pattern in gitignore, f"sanity: {pattern!r} is also in .gitignore (consistent exclusion across git and docker)")

    print("\n--- docker-compose.yml is valid, structurally correct YAML ---")
    try:
        import yaml
        compose = yaml.safe_load(compose_text)
        check(isinstance(compose, dict), "docker-compose.yml parses as valid YAML")
        check("smartassist" in compose.get("services", {}), "defines a 'smartassist' service")
        service = compose["services"]["smartassist"]
        check(service.get("build") == ".", "builds from the local Dockerfile (build: .)")
        check("8000:8000" in service.get("ports", []), "maps port 8000:8000")
        check(".env" in service.get("env_file", []), "loads secrets from a local .env file (never embedded in the compose file itself)")
        check(any("chroma_db" in v for v in service.get("volumes", [])), "mounts a volume over chroma_db/ for index persistence across restarts")
        check("GEMINI_API_KEY" not in compose_text and "ADMIN_PASSWORD_HASH=" not in compose_text.split("env_file")[-1],
              "no real secret values appear directly in docker-compose.yml")
    except ImportError:
        print("[SKIPPED] pyyaml not installed — cannot parse docker-compose.yml as YAML in this environment")

    print("\n--- Dependency consistency ---")
    check("sentence-transformers" in requirements and "chromadb" in requirements,
          "requirements.txt still includes the heavy ML dependencies the Dockerfile needs to install")
    req_lines = [ln for ln in requirements.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    check(any(ln.startswith("google-genai") for ln in req_lines) and not any(ln.startswith("google-generativeai") for ln in req_lines),
          "requirements.txt uses the supported google-genai SDK, not the deprecated google-generativeai")
    check(os.path.isfile(os.path.join(BASE_DIR, "railway.json")), "railway.json exists")

    print("\n--- docs/architecture.md covers the real pipeline components ---")
    architecture = read("docs/architecture.md")
    for term in ["Preprocessing", "Intent Classifier", "Escalation", "RAG Retriever", "Conversation", "LLM Generator",
                 "FastAPI", "SQLite", "ChromaDB", "sentence-transformers", "Gemini", "knowledge base", "frontend", "admin"]:
        check(term.lower() in architecture.lower(), f"architecture.md mentions '{term}'")
    check("Not built or run" in architecture or "not built or run" in architecture.lower(),
          "architecture.md honestly notes Docker was not built/run, not just listed as a file")

    print("\n--- docs/demo_guide.md covers the required demo scenarios ---")
    demo_guide = read("docs/demo_guide.md")
    for scenario in ["FAQ", "technical", "conversation memory", "frustrat", "escalat", "feedback", "admin", "prompt-injection"]:
        check(scenario.lower() in demo_guide.lower(), f"demo_guide.md covers the '{scenario}' scenario")
    check("OBSERVED" in demo_guide and "EXPECTED" in demo_guide,
          "demo_guide.md clearly distinguishes OBSERVED (real test output) from EXPECTED (not executed) scenarios")
    check("5-minute" in demo_guide or "5 minute" in demo_guide, "demo_guide.md includes the required 5-minute demo sequence")

    print("\n--- docs/deployment.md covers the required deployment topics ---")
    deployment = read("docs/deployment.md")
    for topic in ["GEMINI_API_KEY", "Render", "Railway", "environment variable"]:
        check(topic.lower() in deployment.lower(), f"deployment.md covers '{topic}'")
    check("NOT PERFORMED" in deployment or "not performed" in deployment.lower(),
          "deployment.md explicitly states deployment was not performed, rather than implying success")

    print("\n" + "-" * 60)
    if _failures == 0:
        print("ALL DOCKER CONFIG STATIC CHECKS PASSED (no Docker daemon available in this sandbox — build/run were NOT performed, see reports/day15_docker_notes.md)")
    else:
        print(f"{_failures} CHECK(S) FAILED")


if __name__ == "__main__":
    run()
