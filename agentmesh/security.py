"""Secret redaction. Applied to everything AgentMesh persists or prints from workers."""
from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)(authorization|proxy-authorization)\s*[:=]\s*(bearer|basic)?\s*[^\s\"']+"), r"\1: [REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"), "Bearer [REDACTED]"),
    (re.compile(r"(?i)(set-)?cookie\s*[:=]\s*[^\n]+"), "cookie: [REDACTED]"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(api[_-]?key|secret|token|passwd|password|credential)[A-Z0-9_]*)\s*[:=]\s*[\"']?[^\s\"',;]{4,}"),
     r"\1=[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"), "[REDACTED_KEY]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"), "[REDACTED_KEY]"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "[REDACTED_KEY]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED_JWT]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S), "[REDACTED_PRIVATE_KEY]"),
]


def redact(text: str) -> str:
    for pat, repl in _RULES:
        text = pat.sub(repl, text)
    return text
