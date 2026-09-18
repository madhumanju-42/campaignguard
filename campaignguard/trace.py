"""Sanitized agent-trace helpers. Traces record tool requests/results, never secrets or hidden reasoning."""

from __future__ import annotations

import re
from typing import Any

SECRET_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|Bearer\s+[A-Za-z0-9._\-]+|api[_-]?key\s*[:=]\s*\S+)", re.I)
MAX_STR = 300


def sanitize(value: Any, max_str: int = MAX_STR) -> Any:
    """Redact secret-like strings, truncate long strings, and drop reasoning fields."""
    if isinstance(value, str):
        v = SECRET_RE.sub("[REDACTED]", value)
        return v if len(v) <= max_str else v[:max_str] + f"…[+{len(v) - max_str} chars]"
    if isinstance(value, dict):
        return {k: sanitize(v, max_str) for k, v in value.items()
                if k not in {"reasoning", "encrypted_content", "api_key", "authorization", "headers"}}
    if isinstance(value, list):
        return [sanitize(v, max_str) for v in value if not (isinstance(v, dict) and v.get("type") == "reasoning")]
    return value
