"""
Day 6: LLM provider layer.

This module isolates everything provider-specific (Gemini today, maybe
OpenAI later) behind one small interface: LLMProvider.generate(...).
Nothing outside this file should ever import `google.generativeai` or
`openai` directly — response_generator.py only talks to LLMProvider.

That means swapping providers later (or adding a second one) means
writing one new class here, not touching prompt logic, retrieval, or
FastAPI routes.

ENVIRONMENT NOTE: this sandbox has no internet access and no API key
configured, so a real Gemini API call could NOT be executed here. This
file was written and syntax-checked, and its error-handling paths were
tested with a FAKE provider (see tests/test_llm_provider.py /
tests/test_response_generator.py) — not a real network call. You must
set a real GEMINI_API_KEY and test a live call locally (see README.md).
"""

import os
from abc import ABC, abstractmethod

from app.config import (
    GEMINI_API_KEY_ENV_VAR,
    GEMINI_MODEL_NAME,
    LLM_REQUEST_TIMEOUT_SECONDS,
)


# --- Custom exceptions -------------------------------------------------
# response_generator.py catches these to decide on fallback behavior,
# instead of catching a broad, provider-specific exception type.

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


# --- Provider interface --------------------------------------------------

class LLMProvider(ABC):
    """
    Any LLM provider must implement generate(). Keeping the system
    instructions and the user-facing prompt as SEPARATE arguments (rather
    than one pre-joined string) matters for security: it lets each
    provider use its native "system role" / "system_instruction" feature,
    which keeps our behavioral rules structurally separated from
    untrusted user/knowledge-base text where the API supports it.
    """

    @abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """Returns the model's reply text, or raises an LLMError subclass."""
        raise NotImplementedError


# --- Gemini implementation ------------------------------------------------

class GeminiProvider(LLMProvider):
    def __init__(self, api_key: str = None, model_name: str = GEMINI_MODEL_NAME):
        self.api_key = api_key or os.environ.get(GEMINI_API_KEY_ENV_VAR)
        self.model_name = model_name
        self._model = None  # lazy: only built on first real call

    def _get_model(self):
        if not self.api_key:
            raise MissingAPIKeyError(
                f"{GEMINI_API_KEY_ENV_VAR} is not set. Add it to your .env file "
                f"(see .env.example) — never hard-code it in source code."
            )

        if self._model is None:
            try:
                import google.generativeai as genai
            except ImportError as exc:
                raise LLMAPIError(
                    "google-generativeai is not installed. Run: "
                    "pip install google-generativeai"
                ) from exc

            genai.configure(api_key=self.api_key)
            self._model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=None,  # system prompt is passed per-call in generate()
            )
        return self._model

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        model = self._get_model()

        # Rebuild the model with the current system_instruction if it
        # differs from last time — cheap because the SDK object itself
        # is lightweight; avoids caching a stale system prompt.
        try:
            import google.generativeai as genai

            model = genai.GenerativeModel(
                model_name=self.model_name,
                system_instruction=system_prompt,
            )
            response = model.generate_content(
                user_prompt,
                request_options={"timeout": LLM_REQUEST_TIMEOUT_SECONDS},
            )
        except MissingAPIKeyError:
            raise
        except Exception as exc:
            message = str(exc).lower()
            if "timeout" in message or "deadline" in message:
                raise LLMTimeoutError(f"Gemini request timed out: {exc}") from exc
            if "rate limit" in message or "quota" in message or "429" in message:
                raise LLMRateLimitError(f"Gemini rate limit/quota error: {exc}") from exc
            raise LLMAPIError(f"Gemini API error: {exc}") from exc

        text = getattr(response, "text", None)
        if not text or not text.strip():
            raise EmptyResponseError("Gemini returned an empty response")

        return text.strip()


def get_default_provider() -> LLMProvider:
    """
    Returns the configured provider (see app.config.LLM_PROVIDER).
    Only Gemini is implemented for Day 6, per the brief's requirement to
    pick one primary provider — the structure supports adding an
    OpenAIProvider class here later without touching any other file.
    """
    from app.config import LLM_PROVIDER

    if LLM_PROVIDER == "gemini":
        return GeminiProvider()

    raise LLMAPIError(f"Unsupported LLM_PROVIDER configured: {LLM_PROVIDER!r}")
