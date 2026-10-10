"""
LLM provider layer (Gemini via the current `google-genai` SDK).

Everything provider-specific lives here; response_generator.py only talks
to LLMProvider.generate().

Changes vs. the original:
- Migrated from the deprecated `google-generativeai` package to the
  supported `google-genai` SDK, and from the RETIRED `gemini-1.5-flash`
  model to `gemini-2.5-flash` (configurable via GEMINI_MODEL).
- The client is created ONCE and reused (the old code rebuilt a
  GenerativeModel on every call).
- Per-attempt timeout is set on the client (configurable, see config.py).
- At most LLM_MAX_RETRIES (default 1) retry, only for transient HTTP
  errors (429/5xx) - never for timeouts - and only if the total time
  budget allows. No unbounded retry loops.
- Output tokens are capped and Gemini 2.5 "thinking" is disabled by
  default (it adds seconds of latency that KB-grounded support answers
  don't need).

The API key is read from the GEMINI_API_KEY environment variable only.
"""

import os
import threading
import time
from abc import ABC, abstractmethod

from app.config import (
    GEMINI_API_KEY_ENV_VAR,
    GEMINI_MODEL_NAME,
    GEMINI_THINKING_BUDGET,
    LLM_MAX_OUTPUT_TOKENS,
    LLM_MAX_RETRIES,
    LLM_REQUEST_TIMEOUT_SECONDS,
    LLM_RETRY_BACKOFF_SECONDS,
    LLM_TEMPERATURE,
    LLM_TOTAL_BUDGET_SECONDS,
)


class LLMError(Exception):
    """Base class for all LLM-related errors."""


class MissingAPIKeyError(LLMError):
    """Raised when the required API key environment variable isn't set."""


class LLMTimeoutError(LLMError):
    """Raised when the LLM API does not respond within the configured timeout."""


class LLMRateLimitError(LLMError):
    """Raised when the LLM API reports a rate limit / quota error."""


class LLMAPIError(LLMError):
    """Raised for any other API failure (network error, 5xx, invalid request, etc.)."""


class EmptyResponseError(LLMError):
    """Raised when the API call succeeds but returns no usable text."""


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Returns the model's reply text, or raises an LLMError subclass."""
        raise NotImplementedError


_TRANSIENT_CODES = {429, 500, 502, 503, 504}


def _status_code(exc: Exception):
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    code = getattr(exc, "status_code", None)
    return code if isinstance(code, int) else None


def _classify(exc: Exception) -> LLMError:
    """Maps an SDK/network exception to one of our error types."""
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    code = _status_code(exc)

    if "timeout" in name or "timeout" in message or "timed out" in message or "deadline" in message or code == 504:
        return LLMTimeoutError(f"Gemini request timed out ({type(exc).__name__})")
    if code == 429 or "resource_exhausted" in message or "quota" in message or "rate limit" in message:
        return LLMRateLimitError(f"Gemini rate limit/quota error ({type(exc).__name__}, code={code})")
    # Do not echo the raw message: SDK errors can include request details.
    return LLMAPIError(f"Gemini API error ({type(exc).__name__}, code={code})")


class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str = None, model_name: str = None):
        self.api_key = api_key or os.environ.get(GEMINI_API_KEY_ENV_VAR)
        self.model_name = model_name or GEMINI_MODEL_NAME
        self._client = None
        self._lock = threading.Lock()

    def _get_client(self):
        if not self.api_key:
            # Re-check the environment so a key added after construction is picked up.
            self.api_key = os.environ.get(GEMINI_API_KEY_ENV_VAR)
        if not self.api_key:
            raise MissingAPIKeyError(
                f"{GEMINI_API_KEY_ENV_VAR} is not set. Add it as an environment "
                f"variable (Railway: Variables tab) - never hard-code it."
            )
        if self._client is None:
            with self._lock:
                if self._client is None:
                    try:
                        from google import genai
                        from google.genai import types
                    except ImportError as exc:
                        raise LLMAPIError(
                            "google-genai is not installed. Run: pip install google-genai"
                        ) from exc
                    # HttpOptions.timeout is in MILLISECONDS.
                    self._client = genai.Client(
                        api_key=self.api_key,
                        http_options=types.HttpOptions(timeout=int(LLM_REQUEST_TIMEOUT_SECONDS * 1000)),
                    )
        return self._client

    def _build_config(self, system_prompt: str):
        from google.genai import types

        kwargs = dict(
            system_instruction=system_prompt,
            max_output_tokens=LLM_MAX_OUTPUT_TOKENS,
            temperature=LLM_TEMPERATURE,
        )
        # Thinking can only be switched off on 2.5 Flash / Flash-Lite;
        # 2.5 Pro and non-2.5 models reject or ignore it.
        if "2.5-flash" in self.model_name:
            kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=GEMINI_THINKING_BUDGET)
        return types.GenerateContentConfig(**kwargs)

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()  # raises MissingAPIKeyError
        started = time.perf_counter()
        attempt = 0
        response = None

        while True:
            try:
                response = client.models.generate_content(
                    model=self.model_name,
                    contents=user_prompt,
                    config=self._build_config(system_prompt),
                )
                break
            except MissingAPIKeyError:
                raise
            except Exception as exc:
                error = _classify(exc)
                elapsed = time.perf_counter() - started
                can_retry = (
                    attempt < LLM_MAX_RETRIES
                    and _status_code(exc) in _TRANSIENT_CODES
                    and not isinstance(error, LLMTimeoutError)
                    and elapsed + LLM_RETRY_BACKOFF_SECONDS + 1.0 < LLM_TOTAL_BUDGET_SECONDS
                )
                if not can_retry:
                    raise error from exc
                attempt += 1
                time.sleep(LLM_RETRY_BACKOFF_SECONDS)

        text = getattr(response, "text", None)
        if not text or not text.strip():
            raise EmptyResponseError("Gemini returned an empty response")
        return text.strip()


# One provider (and therefore one SDK client) for the whole process.
_default_provider = None
_default_provider_lock = threading.Lock()


def get_default_provider() -> LLMProvider:
    """Returns the shared, lazily created provider (see config.LLM_PROVIDER)."""
    global _default_provider
    from app.config import LLM_PROVIDER

    if LLM_PROVIDER != "gemini":
        raise LLMAPIError(f"Unsupported LLM_PROVIDER configured: {LLM_PROVIDER!r}")

    if _default_provider is None:
        with _default_provider_lock:
            if _default_provider is None:
                _default_provider = GeminiProvider()
    return _default_provider


def reset_default_provider() -> None:
    """Test helper: forget the cached provider (e.g. after changing env vars)."""
    global _default_provider
    with _default_provider_lock:
        _default_provider = None
