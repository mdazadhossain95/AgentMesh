from __future__ import annotations

from pathlib import Path

from agentmesh.config import ProjectPaths
from agentmesh.reference import resolve_reference


def _fake_run(calls: list):
    """Stand-in for subprocess.run: no network, no real git. Records every invocation."""
    def run(cmd, capture_output=True, text=True):
        calls.append(cmd)
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        r = R()
        if cmd[:2] == ["git", "clone"]:
            target = Path(cmd[-1])
            target.mkdir(parents=True, exist_ok=True)
            (target / "marker.txt").write_text(cmd[-2])   # remember which URL this clone was "for"
        elif cmd[1] == "-C" and cmd[3] == "remote":
            marker = Path(cmd[2]) / "marker.txt"
            r.stdout = marker.read_text() if marker.is_file() else ""
            r.returncode = 0 if marker.is_file() else 1
        return r
    return run


def test_same_basename_different_hosts_do_not_collide(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr("agentmesh.reference.subprocess.run", _fake_run(calls))
    paths = ProjectPaths(tmp_path)

    a = resolve_reference("https://github.com/org-a/skills", paths)
    b = resolve_reference("https://github.com/org-b/skills", paths)

    assert a != b
    assert (a / "marker.txt").read_text() == "https://github.com/org-a/skills"
    assert (b / "marker.txt").read_text() == "https://github.com/org-b/skills"
    assert sum(1 for c in calls if c[:2] == ["git", "clone"]) == 2


def test_stale_cache_with_wrong_origin_is_replaced(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr("agentmesh.reference.subprocess.run", _fake_run(calls))
    paths = ProjectPaths(tmp_path)
    ref = "https://github.com/anthropics/skills"

    target = resolve_reference(ref, paths)
    (target / "marker.txt").write_text("https://evil.example.com/anthropics/skills")  # tamper: wrong origin now

    target2 = resolve_reference(ref, paths)
    assert target2 == target
    assert (target2 / "marker.txt").read_text() == ref     # re-cloned, not served stale/mismatched content
    assert sum(1 for c in calls if c[:2] == ["git", "clone"]) == 2
