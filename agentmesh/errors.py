"""Normalized error codes and AgentMesh exceptions."""
from __future__ import annotations

import re
from enum import Enum


class ErrorCode(str, Enum):
    QUOTA_EXCEEDED = "QUOTA_EXCEEDED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTH_FAILED = "AUTH_FAILED"
    CLI_NOT_FOUND = "CLI_NOT_FOUND"
    TIMEOUT = "TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    WORKER_FAILED = "WORKER_FAILED"
    PROJECT_ERROR = "PROJECT_ERROR"
    GIT_CONFLICT = "GIT_CONFLICT"
    UNSUPPORTED = "UNSUPPORTED"
    NO_WORKER_AVAILABLE = "NO_WORKER_AVAILABLE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"


# Failures that say "this worker cannot serve right now", not "the work is bad".
# Falling over to another worker is safe for these.
INFRA_ERRORS = frozenset({
    ErrorCode.QUOTA_EXCEEDED,
    ErrorCode.RATE_LIMITED,
    ErrorCode.AUTH_FAILED,
    ErrorCode.CLI_NOT_FOUND,
    ErrorCode.TIMEOUT,
    ErrorCode.PROVIDER_UNAVAILABLE,
    ErrorCode.UNSUPPORTED,
})

# Order matters: first match wins. Patterns are matched on lowercased output.
_PATTERNS: list[tuple[ErrorCode, re.Pattern[str]]] = [
    (ErrorCode.QUOTA_EXCEEDED, re.compile(
        r"quota|usage limit|insufficient[_ ]credit|credit balance|out of credits|"
        r"billing|exceeded your (current|daily|monthly)|limit reached|plan limit|"
        r"resource[_ ]exhausted|you('ve| have) hit your")),
    (ErrorCode.RATE_LIMITED, re.compile(
        r"rate[ _-]?limit|too many requests|\b429\b|slow down|throttl")),
    (ErrorCode.AUTH_FAILED, re.compile(
        r"unauthori[sz]ed|\b401\b|\b403\b|not (logged|signed) in|please (log|sign) ?in|"
        r"login required|invalid (api[ _-]?key|token|credentials)|authentication (failed|error|required)|"
        r"failed to authenticate|"
        r"api key (is )?(missing|not set|required)|run .{0,20}login")),
    (ErrorCode.PROVIDER_UNAVAILABLE, re.compile(
        r"\b50[234]\b|service unavailable|overloaded|temporarily unavailable|"
        r"bad gateway|gateway timeout|connection (reset|refused)|econnreset|enotfound|"
        r"network error|could not resolve")),
]


def classify_text(text: str) -> ErrorCode | None:
    """Map provider-specific failure text to a normalized code, or None."""
    low = text.lower()
    for code, pat in _PATTERNS:
        if pat.search(low):
            return code
    return None


_UNITS = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600,
          "d": 86400, "day": 86400}
_RETRY_AFTER = re.compile(r"retry[- ]after[:= ]+(\d+)")
_IN_TIME = re.compile(r"\b(?:in|after|within)\s+(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|s|m|h|d)\b")


def parse_retry_seconds(text: str) -> int | None:
    """Reset/retry delay announced by the provider itself ('try again in 2 hours', 'Retry-After: 30'), clamped to
    30s..7d. None when the message gives no duration: callers then use their configured default."""
    low = text.lower()
    m = _RETRY_AFTER.search(low)
    if m:
        secs = float(m.group(1))
    else:
        m = _IN_TIME.search(low)
        if not m:
            return None
        secs = float(m.group(1)) * _UNITS[m.group(2).rstrip("s") if m.group(2) not in ("s",) else "s"]
    return int(min(max(secs, 30), 7 * 86400))


class AgentMeshError(Exception):
    code: ErrorCode = ErrorCode.UNKNOWN_ERROR

    def __init__(self, message: str, code: ErrorCode | None = None):
        super().__init__(message)
        if code is not None:
            self.code = code


class ProjectError(AgentMeshError):
    code = ErrorCode.PROJECT_ERROR


class UnsupportedError(AgentMeshError):
    code = ErrorCode.UNSUPPORTED


class GitError(AgentMeshError):
    code = ErrorCode.PROJECT_ERROR
