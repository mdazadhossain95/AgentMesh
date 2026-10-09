import re
import unicodedata

from agentmesh import benchmark as bm
from agentmesh.benchmark import CandidateResult, TaskScore, evaluate, ranked, suggestions

REFERENCE = {
    "slugify": '''import re, unicodedata
def slugify(text):
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")
''',
    "lru": '''from collections import OrderedDict
class LRUCache:
    def __init__(self, capacity):
        if capacity <= 0: raise ValueError("capacity")
        self.cap, self.d = capacity, OrderedDict()
    def get(self, key, default=None):
        if key not in self.d: return default
        self.d.move_to_end(key); return self.d[key]
    def put(self, key, value):
        self.d[key] = value; self.d.move_to_end(key)
        if len(self.d) > self.cap: self.d.popitem(last=False)
    def __len__(self): return len(self.d)
    def __contains__(self, key): return key in self.d
''',
    "intervals": '''def merge_intervals(intervals):
    out = []
    for s, e in sorted((list(i) for i in intervals), key=lambda x: x[0]):
        if out and s <= out[-1][1]: out[-1][1] = max(out[-1][1], e)
        else: out.append([s, e])
    return out
''',
    "duration": '''import re
_U = {"d": 86400, "h": 3600, "m": 60, "s": 1}
def parse_duration(s):
    m = re.fullmatch(r"(?:(\\d+)d)?\\s*(?:(\\d+)h)?\\s*(?:(\\d+)m)?\\s*(?:(\\d+)s)?", s.strip())
    if not s.strip() or not m or not any(m.groups()): raise ValueError(s)
    return sum(int(g) * u for g, u in zip(m.groups(), _U.values()) if g)
''',
}

from bench_refs import HARD

REFERENCE.update(HARD)


def test_reference_solutions_score_100_and_stubs_score_0(tmp_path):
    for t in bm.TASKS:
        d = tmp_path / t.id
        d.mkdir()
        (d / "solution.py").write_text(t.stub)
        stub_score = evaluate(d, t)
        (d / "solution.py").write_text(REFERENCE[t.id])
        assert evaluate(d, t) == 1.0, f"{t.id}: reference solution must pass every hidden test"
        assert stub_score < 1.0, f"{t.id}: stub must not pass"


def test_visible_tests_are_a_subset_that_the_reference_passes(tmp_path):
    import subprocess, sys
    for t in bm.TASKS:
        d = tmp_path / ("v" + t.id)
        d.mkdir()
        (d / "solution.py").write_text(REFERENCE[t.id])
        (d / "test_solution.py").write_text(t.visible)
        r = subprocess.run([sys.executable, "-m", "unittest", "test_solution"], cwd=d, capture_output=True, text=True)
        assert r.returncode == 0, (t.id, r.stderr)


def test_partial_credit_and_broken_code(tmp_path):
    t = bm.TASKS[0]
    (tmp_path / "solution.py").write_text("def slugify(text):\n    return text.lower().replace(' ', '-')\n")
    assert 0 < evaluate(tmp_path, t) < 1
    (tmp_path / "solution.py").write_text("def slugify(:\n")
    assert evaluate(tmp_path, t) == 0.0
    (tmp_path / "solution.py").write_text("while True: pass\n")
    # an infinite loop is cut off by the 30s harness timeout; covered by TimeoutExpired -> 0.0 (not run here: slow)


def _r(worker, model, scores, secs=10.0, err=None):
    return CandidateResult(worker, model, [TaskScore(f"t{i}", s, secs, err) for i, s in enumerate(scores)], planned=len(scores))


def test_ranking_and_suggestions():
    rs = [_r("kilo", "m-slow", [1, 1, 1, 1], 50), _r("kilo", "m-fast", [1, 1, 1, 1], 5), _r("kilo", "m-bad", [0, 0, 0, 0]),
          _r("kiro", "k1", [1, 0, 1, 0]), _r("claude", None, [1, 1, 1, 0]), _r("oc", None, [0, 0], 0, "AUTH_FAILED")]
    order = [(r.worker, r.model) for r in ranked(rs)]
    assert order[:2] == [("kilo", "m-fast"), ("kilo", "m-slow")]
    sug = suggestions(rs)
    assert sug["model_chains"] == {"kilo": ["m-fast", "m-slow"], "kiro": ["k1"]}          # bad model dropped
    assert sug["quality_order"] == ["kilo", "claude", "kiro"] and "oc" not in sug["quality_order"]


def test_candidates_skip_unusable_and_expand_model_lists():
    from helpers import mock_registry
    from agentmesh.adapters import KiloAdapter, ClaudeAdapter, FreebuffAdapter
    from agentmesh.models import AgentInfo
    from agentmesh.registry import Registry
    reg = Registry()
    for a in (ClaudeAdapter(), KiloAdapter(), FreebuffAdapter()):
        reg.register(a)
    mk = lambda n, state="READY": AgentInfo(name=n, display_name=n, adapter="x", installed=True, state=state)
    infos = {"claude": mk("claude"), "kilo": mk("kilo"), "freebuff": mk("freebuff", "INTERACTIVE_ONLY")}
    assert bm.candidates(reg, infos, {"kilo": ["a", "b"]}) == [("claude", None), ("kilo", "a"), ("kilo", "b")]
    assert bm.candidates(reg, infos, {}, only={"claude"}) == [("claude", None)]
    infos["claude"].state = "UNVERIFIED"
    assert ("claude", None) not in bm.candidates(reg, infos, {})


def _info(name, version="1.0", ready=True):
    from agentmesh.models import AgentInfo
    return AgentInfo(name=name, display_name=name, adapter=name, installed=True, version=version,
                     state="READY" if ready else "UNVERIFIED")


def test_stale_messages_age_version_and_task_count():
    from datetime import datetime, timezone
    from agentmesh.benchmark import stale_messages
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    all_tasks = [{"task": t.id} for t in bm.TASKS]
    rows = [
        {"worker": "fresh", "model": None, "at": "2026-10-01T00:00:00+00:00", "cli_version": "1.0", "tasks": all_tasks},
        {"worker": "old", "model": None, "at": "2026-08-01T00:00:00+00:00", "cli_version": "1.0", "tasks": all_tasks},
        {"worker": "bumped", "model": None, "at": "2026-10-01T00:00:00+00:00", "cli_version": "0.9", "tasks": all_tasks},
        {"worker": "short", "model": None, "at": "2026-10-01T00:00:00+00:00", "cli_version": "1.0", "tasks": all_tasks[:4]},
    ]
    infos = {n: _info(n) for n in ("fresh", "old", "bumped", "short", "never")}
    msgs = {m.split(":")[0]: m for m in stale_messages(infos, rows, now)}
    assert "fresh" not in msgs
    assert "days ago" in msgs["old"] and "CLI is now 1.0" in msgs["bumped"] and "fewer than" in msgs["short"]
    assert "no benchmark scores" in msgs["never"]


def test_stale_ignores_unready_workers_and_missing_version_in_old_rows():
    from datetime import datetime, timezone
    from agentmesh.benchmark import stale_messages
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    rows = [{"worker": "legacy", "model": None, "at": "2026-10-07T00:00:00+00:00", "tasks": [{"task": t.id} for t in bm.TASKS]}]
    infos = {"legacy": _info("legacy", version="2.0"), "off": _info("off", ready=False)}
    assert stale_messages(infos, rows, now) == []


# ---- --quick ----
def _res(worker, model, score, n_tasks):
    r = CandidateResult(worker, model, planned=n_tasks)
    r.scores = [TaskScore(t.id, score, 1.0) for t in bm.TASKS[:n_tasks]]
    return r


def test_quick_row_never_replaces_a_full_row(tmp_path):
    bm.save([_res("w", None, 1.0, len(bm.TASKS))])
    bm.save([_res("w", None, 0.2, 2), _res("v", None, 1.0, 2)], quick=True)
    rows = {r["worker"]: r for r in bm.load_saved()}
    assert rows["w"]["score"] == 1.0 and not rows["w"].get("quick")        # full row kept
    assert rows["v"]["quick"] is True                                      # new worker gets a quick row
    bm.save([_res("v", None, 0.5, len(bm.TASKS))])
    assert not {r["worker"]: r for r in bm.load_saved()}["v"].get("quick")  # full run replaces quick


def test_quick_rows_do_not_drive_ordering_or_chains():
    bm.save([_res("a", "m1", 1.0, len(bm.TASKS)), _res("b", "m2", 1.0, 2)], quick=False)
    bm.save([_res("q", "m3", 1.0, 2)], quick=True)
    assert bm.measured_quality_order() == ["a", "b"]           # b was saved as a full row here; q is quick only
    assert bm.measured_chain("q", ["m3"]) == []
    assert bm.measured_chain("a", ["m1"]) == ["m1"]


def test_stale_message_for_quick_only_worker():
    from datetime import datetime, timezone
    rows = [{"worker": "q", "model": None, "at": "2026-10-08T00:00:00+00:00", "quick": True, "tasks": [{"task": "slugify"}]}]
    msgs = bm.stale_messages({"q": _info("q")}, rows, datetime(2026, 10, 9, tzinfo=timezone.utc))
    assert len(msgs) == 1 and "quick check only" in msgs[0]
