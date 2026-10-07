"""Path-scope checks: compare what git says changed against a role's allowed/forbidden globs."""
from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=512)
def _compile(pattern: str) -> re.Pattern[str]:
    pat = pattern[2:] if pattern.startswith("./") else pattern      # NOT lstrip("./"): that also eats the dot of ".env"
    if pat.endswith("/"):
        pat += "**"
    out, i = "", 0
    while i < len(pat):
        c = pat[i]
        if pat[i:i + 3] == "**/":
            out += "(?:.*/)?"; i += 3; continue
        if pat[i:i + 2] == "**":
            out += ".*"; i += 2; continue
        out += "[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c)
        i += 1
    return re.compile(out + r"\Z")


def matches(path: str, patterns: list[str]) -> bool:
    path = path[2:] if path.startswith("./") else path
    return any(_compile(p).match(path) for p in patterns)


def violations(changed: list[str], allowed: list[str], forbidden: list[str], read_only: bool) -> list[str]:
    out = []
    for f in changed:
        if read_only:
            out.append(f"read-only role modified {f}")
        elif matches(f, forbidden):
            out.append(f"forbidden path modified: {f}")
        elif allowed and not matches(f, allowed):
            out.append(f"outside allowed paths: {f}")
    return out
