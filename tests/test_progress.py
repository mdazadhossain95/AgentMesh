import time

from agentmesh.progress import Heartbeat, fmt, typical_seconds

ROWS = [{"worker": "kiro", "model": "auto", "avg_seconds": 13.0},
        {"worker": "kiro", "model": "glm-5", "avg_seconds": 15.0}]


def test_typical_exact_and_fallback():
    assert typical_seconds("kiro", "glm-5", ROWS) == 15.0
    assert typical_seconds("kiro", "other", ROWS) == 14.0
    assert typical_seconds("codex", None, ROWS) is None


def test_fmt():
    assert fmt(9) == "9s" and fmt(125) == "2m05s"


def test_heartbeat_emits_start_ticks_finish():
    lines = []
    with Heartbeat(lines.append, "[t1] codex/default", 300, typical=20, interval=0.05):
        time.sleep(0.2)
    assert "started" in lines[0] and "baseline ~20s" in lines[0]
    assert any("running" in l and "left" in l for l in lines[1:-1])
    assert "finished" in lines[-1]
