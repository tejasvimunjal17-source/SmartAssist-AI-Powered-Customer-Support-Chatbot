"""
Deployment consistency check.

Production bug this guards against:
    AttributeError: 'GeneratedResponse' object has no attribute 'fallback_reason'
raised in app/chat_orchestrator.py. That happens when the files in a deployed
image come from different revisions (e.g. a newer chat_orchestrator.py that reads
`generated.fallback_reason` next to an older response_generator.py whose
GeneratedResponse lacks the field).

This script reads the SOURCE of the consumers with `ast` (so it never has to
guess field names) and verifies that every attribute they read exists on the
dataclass that provides it:

    app/chat_orchestrator.py : generated.<attr>  -> GeneratedResponse fields
    app/main.py              : result.<attr>     -> ChatResult fields

Exit code 0 = consistent, 1 = mismatch (the Docker build then fails instead of
shipping a broken revision).

Run:  python -m scripts.check_response_contract
"""

import ast
import dataclasses
import os
import sys

from app.chat_orchestrator import ChatResult
from app.config import BASE_DIR
from app.response_generator import GeneratedResponse


def attributes_read(relpath: str, variable: str) -> set:
    with open(os.path.join(BASE_DIR, relpath), "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=relpath)
    return {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == variable
    }


def missing(relpath: str, variable: str, cls) -> set:
    provided = {f.name for f in dataclasses.fields(cls)} | {
        n for n in dir(cls) if not n.startswith("_")
    }
    return attributes_read(relpath, variable) - provided


def main() -> int:
    problems = {
        "app/chat_orchestrator.py reads generated.X not on GeneratedResponse": missing(
            "app/chat_orchestrator.py", "generated", GeneratedResponse
        ),
        "app/main.py reads result.X not on ChatResult": missing("app/main.py", "result", ChatResult),
    }
    failed = False
    for description, names in problems.items():
        if names:
            failed = True
            print(f"MISMATCH: {description}: {sorted(names)}")
    if failed:
        print("Deployed files are from different revisions. Re-upload app/ completely.")
        return 1
    print("OK: GeneratedResponse and ChatResult match the code that consumes them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
