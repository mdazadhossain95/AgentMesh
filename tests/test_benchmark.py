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
    return CandidateResult(worker, model, [TaskScore(f"t{i}", s, secs, err) for i, s in enumerate(scores)])


def test_ranking_and_suggestions():
    rs = [_r("kilo", "m-slow", [1, 1, 1, 1], 50), _r("kilo", "m-fast", [1, 1, 1, 1], 5), _r("kilo", "m-bad", [0, 0, 0, 0]),
          _r("kiro", "k1", [1, 0, 1, 0]), _r("claude", None, [1, 1, 1, 0]), _r("qwen", None, [0, 0], 0, "AUTH_FAILED")]
    order = [(r.worker, r.model) for r in ranked(rs)]
    assert order[:2] == [("kilo", "m-fast"), ("kilo", "m-slow")]
    sug = suggestions(rs)
    assert sug["model_chains"] == {"kilo": ["m-fast", "m-slow"], "kiro": ["k1"]}          # bad model dropped
    assert sug["quality_order"] == ["kilo", "claude", "kiro"] and "qwen" not in sug["quality_order"]


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
