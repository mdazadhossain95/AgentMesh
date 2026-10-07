"""Idempotent file writing with human-edit protection."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

HASH_RE = re.compile(r"agentmesh:generated sha256=([0-9a-f]{12})")


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]


def stamp(body: str, comment: str) -> str:
    """Prefix generated content with a hash of the body. `comment` is 'md' or 'yaml'."""
    tag = f"agentmesh:generated sha256={_digest(body)} (edit freely: modified files are never overwritten)"
    return (f"<!-- {tag} -->\n" if comment == "md" else f"# {tag}\n") + body


def split_stamp(text: str) -> tuple[str | None, str]:
    first, _, rest = text.partition("\n")
    m = HASH_RE.search(first)
    return (m.group(1), rest) if m else (None, text)


def write_generated(path: Path, body: str, comment: str, dry_run: bool = False) -> str:
    """Returns created | updated | unchanged | kept (human-modified)."""
    new = stamp(body, comment)
    if not path.exists():
        if not dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new, encoding="utf-8")
        return "created"
    old = path.read_text(encoding="utf-8")
    if old == new:
        return "unchanged"
    recorded, old_body = split_stamp(old)
    if recorded is None or recorded != _digest(old_body):
        return "kept"           # human edited (or never generated): leave alone
    if not dry_run:
        path.write_text(new, encoding="utf-8")
    return "updated"


def upsert_block(path: Path, begin: str, end: str, block: str, dry_run: bool = False) -> str:
    """Insert/replace a marked block, preserving all other content. Idempotent."""
    wrapped = f"{begin}\n{block.strip()}\n{end}"
    if not path.exists():
        if not dry_run:
            path.write_text(wrapped + "\n", encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    pat = re.compile(re.escape(begin) + r".*?" + re.escape(end), re.S)
    if pat.search(text):
        new = pat.sub(lambda _m: wrapped, text, count=1)
    else:
        new = text.rstrip("\n") + ("\n\n" if text.strip() else "") + wrapped + "\n"
    if new == text:
        return "unchanged"
    if not dry_run:
        path.write_text(new, encoding="utf-8")
    return "updated"
