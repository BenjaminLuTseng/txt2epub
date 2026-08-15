"""Claude-powered analysis: book metadata, chapter-split review, cover design.

Every function here degrades gracefully. If no Anthropic credentials are
available the app still works end to end on heuristics alone — the AI only ever
improves on a result that already exists.
"""

from __future__ import annotations

import json
import os
from typing import Any

MODEL = os.environ.get("TXT2EPUB_MODEL", "claude-opus-5")


class AIUnavailable(RuntimeError):
    """No credentials, or the API could not be reached."""


_client = None
_client_error: str | None = None


def client():
    global _client, _client_error
    if _client is not None:
        return _client
    if _client_error is not None:
        raise AIUnavailable(_client_error)
    try:
        import anthropic

        # Resolves ANTHROPIC_API_KEY, then ANTHROPIC_AUTH_TOKEN, then an
        # `ant auth login` profile. Honours ANTHROPIC_BASE_URL automatically.
        _client = anthropic.Anthropic()
    except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a banner
        _client_error = str(exc)
        raise AIUnavailable(_client_error) from exc
    return _client


def available() -> bool:
    """True only if credentials actually resolved.

    The SDK constructs a client happily with no credentials and does not fail
    until the first request, so checking construction alone reports a false
    positive — inspect the resolved auth instead.
    """
    try:
        c = client()
    except AIUnavailable:
        return False
    return bool(getattr(c, "api_key", None) or getattr(c, "auth_token", None))


def _ask(prompt: str, schema: dict[str, Any], effort: str, max_tokens: int = 8000) -> dict:
    try:
        response = client().messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001
        raise AIUnavailable(f"{type(exc).__name__}: {exc}") from exc

    if response.stop_reason == "refusal":
        raise AIUnavailable("The model declined this request.")
    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text:
        raise AIUnavailable("Empty response from the model.")
    return json.loads(text)


# --------------------------------------------------------------------------
# Book metadata
# --------------------------------------------------------------------------

_METADATA_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Book title in its original language."},
        "author": {"type": "string", "description": "Author name, or empty string if genuinely unknown."},
        "series": {"type": "string", "description": "Series name and number, or empty string."},
        "language": {"type": "string", "enum": ["zh-Hans", "zh-Hant", "en", "ja", "ko", "other"]},
        "description": {"type": "string", "description": "One or two sentences describing the book."},
        "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string", "description": "Where the title and author were found, briefly."},
    },
    "required": ["title", "author", "series", "language", "description", "confidence", "notes"],
    "additionalProperties": False,
}


def detect_metadata(filename: str, head: str, tail: str, headings: list[str]) -> dict:
    prompt = f"""Identify the title and author of this book from a plain-text file.

Filename: {filename}

First part of the file:
<head>
{head}
</head>

End of the file:
<tail>
{tail}
</tail>

First chapter headings found:
{chr(10).join(headings[:15]) or "(none detected)"}

The filename often carries the title and author (patterns like
"《书名》作者.txt", "书名 - 作者.txt", "Title - Author.txt"), but the text itself
takes precedence when the two disagree. Strip site watermarks, download-source
banners, and forum signatures — those are not the author. If the author is
genuinely not recoverable, return an empty string rather than a guess."""
    return _ask(prompt, _METADATA_SCHEMA, effort="low", max_tokens=4000)


# --------------------------------------------------------------------------
# Chapter split review
# --------------------------------------------------------------------------

_CHAPTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["good", "use_regex", "no_chapters"]},
        "regex": {
            "type": "string",
            "description": "Python regex matching a full heading line, anchored with ^ and $. Empty unless verdict is use_regex.",
        },
        "note": {"type": "string", "description": "One short sentence for the user."},
    },
    "required": ["verdict", "regex", "note"],
    "additionalProperties": False,
}


def review_chapters(method: str, headings: list[str], sample_lines: list[str], total: int) -> dict:
    prompt = f"""Review an automatic chapter split of a plain-text book.

Detection method that won: {method}
Chapters found: {total}

Detected headings (first 40):
{chr(10).join(headings[:40])}

Random sample of lines that were NOT treated as headings:
{chr(10).join(sample_lines[:25])}

Decide one of:
- "good": the headings are real chapter headings and nothing obvious was missed.
- "use_regex": the split is wrong or incomplete. Supply a Python regex that
  matches an entire heading line (anchored ^...$, matched against each stripped
  line). Use a non-capturing style; do not include re.MULTILINE syntax.
- "no_chapters": this file has no chapter structure and should stay as one piece.

Prefer "good" unless there is a concrete problem: headings that are actually
body text, an obvious numbering gap, or a heading style visible in the sample
lines that the detector clearly missed."""
    return _ask(prompt, _CHAPTERS_SCHEMA, effort="low", max_tokens=4000)


# --------------------------------------------------------------------------
# Cover design
# --------------------------------------------------------------------------

_COVER_SCHEMA = {
    "type": "object",
    "properties": {
        "palette": {
            "type": "object",
            "properties": {
                "background": {"type": "string", "description": "Hex colour, e.g. #12202e"},
                "background2": {"type": "string", "description": "Second gradient stop, hex."},
                "accent": {"type": "string", "description": "Motif and rule colour, hex."},
                "title": {"type": "string", "description": "Title text colour, hex."},
                "author": {"type": "string", "description": "Author text colour, hex."},
            },
            "required": ["background", "background2", "accent", "title", "author"],
            "additionalProperties": False,
        },
        "layout": {"type": "string", "enum": ["centered", "upper", "lower", "band"]},
        "motif": {
            "type": "string",
            "enum": ["none", "arcs", "rings", "mountains", "grid", "rays", "waves", "bars", "dots", "frame", "horizon"],
        },
        "motif_intensity": {"type": "string", "enum": ["subtle", "medium", "bold"]},
        "typeface": {"type": "string", "enum": ["serif", "sans"]},
        "title_lines": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The title broken into 1-3 display lines at sensible word or phrase boundaries.",
        },
        "subtitle": {"type": "string", "description": "Short subtitle or series line; empty string if none."},
        "author_line": {"type": "string", "description": "Author as it should appear on the cover; empty if unknown."},
        "rationale": {"type": "string", "description": "One sentence on why this design suits the book."},
    },
    "required": [
        "palette",
        "layout",
        "motif",
        "motif_intensity",
        "typeface",
        "title_lines",
        "subtitle",
        "author_line",
        "rationale",
    ],
    "additionalProperties": False,
}


def design_cover(
    title: str,
    author: str,
    language: str,
    description: str,
    opening: str,
    style_hint: str = "",
) -> dict:
    hint = f"\nThe user asked for this direction: {style_hint}\n" if style_hint.strip() else ""
    prompt = f"""Design a book cover as a structured spec. A renderer turns your spec into
a 1600x2400 cover, so every choice below is the actual design.

Title: {title}
Author: {author or "(unknown)"}
Language: {language}
What the book is about: {description or "(unknown)"}

Opening lines of the book:
<opening>
{opening[:1200]}
</opening>
{hint}
Design guidance:
- Choose colours that suit this specific book's subject and mood. Avoid the
  default AI palette of purple-to-blue gradients on dark navy, and avoid
  generic stock-cover teal.
- The two background colours form a vertical gradient; keep them close in hue
  so the field reads as one surface, and keep strong contrast against the title
  colour so the type stays legible.
- Break `title_lines` at real phrase boundaries. For Chinese, break by meaning
  rather than character count, and never leave a single trailing character
  alone on a line. One line is best for short titles; never exceed three.
- Pick the motif that carries meaning for this book, not decoration for its own
  sake. "none" is a legitimate and often strong choice for literary fiction.
- Use serif for literary, historical, and classical work; sans for modern,
  technical, or contemporary work."""
    return _ask(prompt, _COVER_SCHEMA, effort="medium", max_tokens=8000)
