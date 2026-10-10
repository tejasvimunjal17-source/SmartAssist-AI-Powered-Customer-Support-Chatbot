"""
Safe, lightweight request timing.

- Uses time.perf_counter() (monotonic) - never wall-clock time.
- Records ONLY stage names and elapsed milliseconds. It never receives or
  logs message text, API keys, or any customer content.
- One structured (JSON) log line per request on logger "smartassist.perf".

Stages used by the chat pipeline:
    preprocess, history, intent, escalation, retrieval, llm, persist, total
Derived:
    local_ms = total - llm      (time spent in our own code)
    llm_ms                      (time spent waiting for Gemini)
"""

import json
import logging
import time
from contextlib import contextmanager
from typing import Dict, Optional

from app.config import PERF_LOGGING_ENABLED

logger = logging.getLogger("smartassist.perf")


class StageTimer:
    def __init__(self) -> None:
        self._start = time.perf_counter()
        self.stages: Dict[str, float] = {}

    @contextmanager
    def stage(self, name: str):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            # accumulate, in case a stage runs more than once
            self.stages[name] = self.stages.get(name, 0.0) + (time.perf_counter() - t0) * 1000.0

    def add(self, name: str, ms: float) -> None:
        self.stages[name] = self.stages.get(name, 0.0) + ms

    def total_ms(self) -> float:
        return (time.perf_counter() - self._start) * 1000.0

    def summary(self) -> Dict[str, float]:
        total = self.total_ms()
        llm = self.stages.get("llm", 0.0)
        out = {k: round(v, 2) for k, v in self.stages.items()}
        out["total"] = round(total, 2)
        out["llm_wait"] = round(llm, 2)
        out["local"] = round(max(total - llm, 0.0), 2)
        return out

    def log(self, extra: Optional[dict] = None) -> None:
        if not PERF_LOGGING_ENABLED:
            return
        record = {"event": "chat_timing", "ms": self.summary()}
        if extra:
            # callers pass only non-sensitive flags (intent name, used_llm, ...)
            record.update(extra)
        logger.info(json.dumps(record, sort_keys=True))
