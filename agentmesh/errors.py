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
