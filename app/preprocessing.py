"""
Text preprocessing module for SmartAssist.

Responsibilities (Day 3 of the project brief):
- Tokenization
- Lemmatization
- Stop-word removal

This module is deliberately kept independent of FastAPI. It will be
imported and reused by:
- the intent classifier (Day 7)
- the RAG pipeline's query cleaning step (Day 5)

Design choice: preprocessing NEVER modifies or discards the user's
original message. It only produces an additional, cleaned representation
alongside it. The original text is what gets shown back to the user and
what gets stored in conversation history later — the cleaned version is
an internal tool for matching/classification.
"""

from dataclasses import dataclass, field
from typing import List

import spacy
from spacy.language import Language

_MODEL_NAME = "en_core_web_sm"
_nlp: Language | None = None


def get_nlp() -> Language:
    """
    Load the spaCy model once and reuse it (loading a model is slow,
    so we don't want to do it on every request).
    """
    global _nlp
    if _nlp is None:
        try:
            _nlp = spacy.load(_MODEL_NAME)
        except OSError as exc:
            raise RuntimeError(
                f"spaCy model '{_MODEL_NAME}' is not installed. "
                f"Run: python -m spacy download {_MODEL_NAME}"
            ) from exc
    return _nlp


@dataclass
class ProcessedText:
    """
    Holds both the original message and its processed representation.
    Nothing about the original is ever destroyed.
    """
    original: str
    tokens: List[str] = field(default_factory=list)
    lemmas: List[str] = field(default_factory=list)
    # Lemmatized, lowercased, with stop words and punctuation removed —
    # this is what later stages (intent matching, RAG query cleanup) use.
    cleaned_tokens: List[str] = field(default_factory=list)

    @property
    def cleaned_text(self) -> str:
        return " ".join(self.cleaned_tokens)


def preprocess(text: str) -> ProcessedText:
    """
    Run tokenization, lemmatization, and stop-word removal on `text`.

    Handles empty/whitespace-only input safely by returning an empty
    (but valid) ProcessedText instead of raising an error — a customer
    support chatbot should never crash just because someone sent a blank
    message.
    """
    if text is None:
        text = ""

    original = text
    text = text.strip()

    if not text:
        return ProcessedText(original=original)

    nlp = get_nlp()
    doc = nlp(text)

    tokens = [tok.text for tok in doc]
    lemmas = [tok.lemma_ for tok in doc]

    # Keep a word only if it is NOT a stop word, NOT pure punctuation,
    # and NOT just whitespace. This preserves meaningful content words
    # (e.g. "refund", "password", "cancel") while dropping noise like
    # "the", "is", "a", commas, etc.
    cleaned_tokens = [
        tok.lemma_.lower()
        for tok in doc
        if not tok.is_stop and not tok.is_punct and not tok.is_space
    ]

    return ProcessedText(
        original=original,
        tokens=tokens,
        lemmas=lemmas,
        cleaned_tokens=cleaned_tokens,
    )
