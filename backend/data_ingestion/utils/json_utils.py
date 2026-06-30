"""Robust JSON parsing for LLM / Ollama text responses."""

from __future__ import annotations

import ast
import json
import re
from typing import Any

_FENCE_RE = re.compile(r"```(?:json|javascript|js)?\s*([\s\S]*?)```", re.IGNORECASE)
_JSON_BLOCK_RE = re.compile(r"(\{[\s\S]*\}|\[[\s\S]*\])")


def parse_llm_json(text: str, *, default: Any = None) -> Any:
    """Parse JSON from LLM output, tolerating fences and minor syntax issues."""
    if text is None:
        return default

    raw = str(text).strip()
    if not raw:
        return default

    candidates: list[str] = [raw]
    fence = _FENCE_RE.search(raw)
    if fence:
        candidates.insert(0, fence.group(1).strip())

    for candidate in candidates:
        parsed = _try_parse(candidate)
        if parsed is not None:
            return parsed

    block = _JSON_BLOCK_RE.search(raw)
    if block:
        parsed = _try_parse(block.group(1))
        if parsed is not None:
            return parsed

    parsed = _try_parse(_sanitize_json(raw))
    if parsed is not None:
        return parsed

    if default is not None:
        return default
    raise ValueError("Unable to parse response as JSON")


def parse_llm_json_object(text: str, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    """Like :func:`parse_llm_json` but always returns a dict."""
    fallback: dict[str, Any] = {} if default is None else default
    try:
        result = parse_llm_json(text, default=fallback)
    except ValueError:
        return fallback
    return result if isinstance(result, dict) else fallback


def _try_parse(text: str) -> Any | None:
    text = text.strip()
    if not text:
        return None

    parsed = _try_load(text)
    if parsed is not None:
        return parsed

    parsed = _try_literal_eval(text)
    if parsed is not None:
        return parsed

    sanitized = _sanitize_json(text)
    if sanitized != text:
        parsed = _try_load(sanitized)
        if parsed is not None:
            return parsed
        parsed = _try_literal_eval(sanitized)
        if parsed is not None:
            return parsed

    return None


def _try_load(text: str) -> Any | None:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _try_literal_eval(text: str) -> Any | None:
    try:
        value = ast.literal_eval(text)
    except (ValueError, SyntaxError):
        return None
    return value if isinstance(value, (dict, list)) else None


def _sanitize_json(text: str) -> str:
    out = text.strip()
    out = re.sub(r"//.*?$", "", out, flags=re.MULTILINE)
    out = re.sub(r"/\*[\s\S]*?\*/", "", out)
    out = (
        out.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )
    out = re.sub(r"\bNone\b", "null", out)
    out = re.sub(r"\bTrue\b", "true", out)
    out = re.sub(r"\bFalse\b", "false", out)
    out = re.sub(r",(\s*[}\]])", r"\1", out)

    def _quote_key(match: re.Match[str]) -> str:
        prefix = match.group(1)
        token = match.group(2)
        if token.startswith('"'):
            return match.group(0)
        if token.startswith("'"):
            return f'{prefix}"{token[1:-1]}":'
        return f'{prefix}"{token}":'

    out = re.sub(
        r"([{,]\s*)(['\"][^'\"\\]*(?:\\.[^'\"\\]*)*|[A-Za-z_][\w.-]*)\s*:",
        _quote_key,
        out,
    )
    out = re.sub(r"'([^'\\]*(?:\\.[^'\\]*)*)'", r'"\1"', out)
    return out
