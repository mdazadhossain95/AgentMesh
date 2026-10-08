"""Measure which worker/model actually solves small coding tasks. LIVE: sends real prompts (uses quota/credits).

Each candidate gets the same tasks in a fresh temp dir containing a stub + visible tests. After the run, HIDDEN tests
(never shown to the worker) are executed against whatever it wrote. Score = fraction of hidden tests passing.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .adapters.base import RunContext
from .adapters.mock import MockAdapter
from .config import global_home
from .models import AgentInfo, Task, utcnow
from .registry import Registry

# CLIs without an edit-only mode need autonomy=full to write files. The dir is an empty temp dir, but these processes can
# still reach your home and network (approve-everything): known tradeoff of benchmarking them.
FULL_ONLY = {"opencode", "kilo", "cline"}
SKIP = {"freebuff"}


@dataclass
class BenchTask:
    id: str
    title: str
    spec: str
    stub: str
    visible: str
    hidden: str


TASKS: list[BenchTask] = [
    BenchTask("slugify", "easy: string handling",
        "Implement slugify(text) -> str: lowercase; transliterate accented letters to ASCII (e.g. 'Café' -> 'cafe'); "
        "replace every run of non-alphanumeric characters with a single '-'; strip leading/trailing '-'; empty input gives ''.",
        "def slugify(text: str) -> str:\n    raise NotImplementedError\n",
        "import unittest\nfrom solution import slugify\n\nclass T(unittest.TestCase):\n"
        "    def test_basic(self):\n        self.assertEqual(slugify('Hello, World!'), 'hello-world')\n",
        textwrap.dedent('''\
        import unittest
        from solution import slugify

        class H(unittest.TestCase):
            def test_basic(self): self.assertEqual(slugify("Hello, World!"), "hello-world")
            def test_spaces(self): self.assertEqual(slugify("  Multiple   spaces "), "multiple-spaces")
            def test_accents(self): self.assertEqual(slugify("Caf\\u00e9 Ol\\u00e9"), "cafe-ole")
            def test_dashes(self): self.assertEqual(slugify("---a---b---"), "a-b")
            def test_empty(self): self.assertEqual(slugify(""), "")
            def test_digits(self): self.assertEqual(slugify("\\u00dcn\\u00efc\\u00f6d\\u00e9 123"), "unicode-123")
            def test_underscore(self): self.assertEqual(slugify("a_b"), "a-b")
            def test_only_symbols(self): self.assertEqual(slugify("!!!"), "")
        ''')),
    BenchTask("lru", "medium: data structure",
        "Implement class LRUCache(capacity). capacity <= 0 raises ValueError. put(key, value) inserts or updates; "
        "get(key, default=None) returns the value or default. Both put and get mark the key most recently used. "
        "When size would exceed capacity, evict the least recently used key. len(cache) is the size; "
        "`key in cache` must NOT change recency.",
        "class LRUCache:\n    pass\n",
        "import unittest\nfrom solution import LRUCache\n\nclass T(unittest.TestCase):\n"
        "    def test_put_get(self):\n        c = LRUCache(2); c.put('a', 1)\n        self.assertEqual(c.get('a'), 1)\n",
        textwrap.dedent('''\
        import unittest
        from solution import LRUCache

        class H(unittest.TestCase):
            def test_put_get(self):
                c = LRUCache(2); c.put("a", 1); self.assertEqual(c.get("a"), 1)
            def test_missing(self):
                c = LRUCache(1); self.assertIsNone(c.get("x")); self.assertEqual(c.get("x", 7), 7)
            def test_evicts_lru(self):
                c = LRUCache(2); c.put("a", 1); c.put("b", 2); c.put("c", 3)
                self.assertNotIn("a", c); self.assertIn("b", c); self.assertIn("c", c)
            def test_get_refreshes(self):
                c = LRUCache(2); c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
                self.assertIn("a", c); self.assertNotIn("b", c)
            def test_update_refreshes(self):
                c = LRUCache(2); c.put("a", 1); c.put("b", 2); c.put("a", 9); c.put("c", 3)
                self.assertEqual(c.get("a"), 9); self.assertNotIn("b", c)
            def test_contains_does_not_refresh(self):
                c = LRUCache(2); c.put("a", 1); c.put("b", 2); _ = "a" in c; c.put("c", 3)
                self.assertNotIn("a", c)
            def test_len(self):
                c = LRUCache(2); c.put("a", 1); c.put("b", 2); c.put("c", 3); self.assertEqual(len(c), 2)
            def test_bad_capacity(self):
                with self.assertRaises(ValueError): LRUCache(0)
        ''')),
    BenchTask("intervals", "bug fix: existing code",
        "merge_intervals has bugs. It must return merged intervals as a list of [start, end] lists sorted by start; "
        "intervals that overlap OR touch (e.g. [1,3] and [3,5]) merge; the input list and its items must not be modified; "
        "empty input returns []. Fix the code in solution.py.",
        "def merge_intervals(intervals):\n    result = []\n    for start, end in intervals:\n"
        "        if result and start < result[-1][1]:\n            result[-1][1] = end\n"
        "        else:\n            result.append([start, end])\n    return result\n",
        "import unittest\nfrom solution import merge_intervals\n\nclass T(unittest.TestCase):\n"
        "    def test_simple(self):\n        self.assertEqual(merge_intervals([[1, 3], [2, 4]]), [[1, 4]])\n",
        textwrap.dedent('''\
        import unittest
        from solution import merge_intervals

        class H(unittest.TestCase):
            def test_simple(self): self.assertEqual(merge_intervals([[1, 3], [2, 4]]), [[1, 4]])
            def test_unsorted(self): self.assertEqual(merge_intervals([[5, 6], [1, 2], [2, 3]]), [[1, 3], [5, 6]])
            def test_touching(self): self.assertEqual(merge_intervals([[1, 3], [3, 5]]), [[1, 5]])
            def test_nested(self): self.assertEqual(merge_intervals([[1, 10], [2, 3]]), [[1, 10]])
            def test_empty(self): self.assertEqual(merge_intervals([]), [])
            def test_gap(self): self.assertEqual(merge_intervals([[1, 2], [4, 5]]), [[1, 2], [4, 5]])
            def test_tuples_in(self): self.assertEqual(merge_intervals([(1, 2), (2, 3)]), [[1, 3]])
            def test_no_mutation(self):
                data = [[3, 4], [1, 3]]; snap = [list(x) for x in data]; merge_intervals(data)
                self.assertEqual(data, snap)
        ''')),
    BenchTask("duration", "medium-hard: parsing + edge cases",
        "Implement parse_duration(s) -> int seconds. Format: optional parts '<n>d', '<n>h', '<n>m', '<n>s' (lowercase, n a "
        "non-negative integer) that must appear in that order d,h,m,s, each at most once; whitespace between parts is allowed. "
        "Examples: '1h30m15s' -> 5415, '90s' -> 90, '2d' -> 172800, '1d2h' -> 93600, '0s' -> 0. Anything else "
        "(empty string, no parts, unknown unit, wrong order, repeated unit, negative or decimal numbers, a bare number) raises ValueError.",
        "def parse_duration(s: str) -> int:\n    raise NotImplementedError\n",
        "import unittest\nfrom solution import parse_duration\n\nclass T(unittest.TestCase):\n"
        "    def test_basic(self):\n        self.assertEqual(parse_duration('90s'), 90)\n",
        textwrap.dedent('''\
        import unittest
        from solution import parse_duration

        class H(unittest.TestCase):
            def test_combo(self): self.assertEqual(parse_duration("1h30m15s"), 5415)
            def test_seconds(self): self.assertEqual(parse_duration("90s"), 90)
            def test_days(self): self.assertEqual(parse_duration("2d"), 172800)
            def test_dh(self): self.assertEqual(parse_duration("1d2h"), 93600)
            def test_zero(self): self.assertEqual(parse_duration("0s"), 0)
            def test_space(self): self.assertEqual(parse_duration("1h 30m"), 5400)
            def test_empty(self):
                with self.assertRaises(ValueError): parse_duration("")
            def test_bare_number(self):
                with self.assertRaises(ValueError): parse_duration("45")
            def test_unknown_unit(self):
                with self.assertRaises(ValueError): parse_duration("1x")
            def test_order(self):
                with self.assertRaises(ValueError): parse_duration("30m1h")
            def test_repeat(self):
                with self.assertRaises(ValueError): parse_duration("1h1h")
            def test_negative_decimal(self):
                with self.assertRaises(ValueError): parse_duration("-1h")
                with self.assertRaises(ValueError): parse_duration("1.5h")
            def test_garbage(self):
                with self.assertRaises(ValueError): parse_duration("abc")
        ''')),
    BenchTask("expr", "hard: parser, precedence",
        "Implement evaluate(expr: str) -> float for arithmetic expressions. Supports + - * / ** and parentheses, unary + and -, "
        "integers and decimals (e.g. '1.5', '.5'), and whitespace anywhere between tokens. / is true division. Precedence: ** "
        "binds tighter than unary minus on its left, so -2**2 is -4; ** is right-associative (2**3**2 is 512) and its exponent "
        "may be signed (2**-1 is 0.5); * / bind tighter than + -, and all of + - * / are left-associative. Division by zero raises "
        "ZeroDivisionError. Any other invalid input (empty or blank, unbalanced parentheses, empty parentheses, a dangling operator, "
        "two numbers in a row, unknown characters, a lone '.') raises ValueError. Do not use eval, exec or ast.",
        "def evaluate(expr: str) -> float:\n    raise NotImplementedError\n",
        "import unittest\nfrom solution import evaluate\n\nclass T(unittest.TestCase):\n"
        "    def test_basic(self):\n        self.assertEqual(evaluate('1+2*3'), 7)\n",
        textwrap.dedent('''\
        import unittest
        from solution import evaluate

        class H(unittest.TestCase):
            def test_prec(self): self.assertEqual(evaluate("1+2*3"), 7)
            def test_parens(self): self.assertEqual(evaluate("(1+2)*3"), 9)
            def test_div(self): self.assertEqual(evaluate("10/4"), 2.5)
            def test_left_assoc(self):
                self.assertEqual(evaluate("8/2/2"), 2); self.assertEqual(evaluate("2-3-4"), -5)
            def test_pow_right_assoc(self): self.assertEqual(evaluate("2**3**2"), 512)
            def test_unary_vs_pow(self): self.assertEqual(evaluate("-2**2"), -4)
            def test_signed_exponent(self): self.assertAlmostEqual(evaluate("2**-1"), 0.5)
            def test_double_unary(self):
                self.assertEqual(evaluate("--3"), 3); self.assertEqual(evaluate("1 - -1"), 2)
            def test_unary_paren(self): self.assertEqual(evaluate("-(2+3)"), -5)
            def test_decimals(self):
                self.assertAlmostEqual(evaluate("1.5*2"), 3.0); self.assertAlmostEqual(evaluate(".5+.5"), 1.0)
            def test_whitespace(self): self.assertEqual(evaluate("  3 "), 3)
            def test_zero_div(self):
                with self.assertRaises(ZeroDivisionError): evaluate("1/0")
            def test_invalid(self):
                for bad in ["", "   ", "1+", "(1", "1)", "1 2", "a", "1+*2", "()", ".", "2*(3+)", "1..2"]:
                    with self.subTest(bad=bad):
                        with self.assertRaises(ValueError): evaluate(bad)
        ''')),
    BenchTask("bucket", "hard: bug fix, several bugs",
        "TokenBucket in solution.py has several bugs. Spec: TokenBucket(capacity, rate, clock=time.monotonic); the bucket starts full; "
        "tokens refill continuously at `rate` per second as reported by clock(), never exceeding capacity; allow(n=1) returns True and "
        "consumes n tokens when at least n are available (exactly n is enough), otherwise returns False and consumes nothing; "
        "n <= 0 raises ValueError; remaining() returns the current token count (as a float, after refilling). "
        "Fix the code in solution.py.",
        "import time\n\n\nclass TokenBucket:\n    def __init__(self, capacity, rate, clock=time.monotonic):\n"
        "        self.capacity = capacity\n        self.rate = rate\n        self.clock = clock\n        self.tokens = 0\n"
        "        self.last = clock()\n\n    def _refill(self):\n        now = self.clock()\n"
        "        self.tokens += (now - self.last) * self.rate\n        self.last = now\n\n"
        "    def allow(self, n=1):\n        self._refill()\n        if self.tokens > n:\n            self.tokens -= n\n"
        "            return True\n        return False\n\n    def remaining(self):\n        return self.tokens\n",
        "import unittest\nfrom solution import TokenBucket\n\nclass T(unittest.TestCase):\n"
        "    def test_starts_full(self):\n        now = [0.0]\n        b = TokenBucket(3, 1, clock=lambda: now[0])\n"
        "        self.assertTrue(b.allow(3))\n",
        textwrap.dedent('''\
        import unittest
        from solution import TokenBucket

        def make(cap, rate):
            now = [0.0]
            return TokenBucket(cap, rate, clock=lambda: now[0]), now

        class H(unittest.TestCase):
            def test_starts_full(self):
                b, _ = make(3, 1); self.assertTrue(b.allow(3))
            def test_exact_is_enough(self):
                b, _ = make(5, 1); self.assertTrue(b.allow(5)); self.assertFalse(b.allow(1))
            def test_denied_consumes_nothing(self):
                b, _ = make(2, 1); self.assertFalse(b.allow(3)); self.assertTrue(b.allow(2))
            def test_refill(self):
                b, now = make(10, 2); b.allow(10); now[0] = 3; self.assertAlmostEqual(b.remaining(), 6.0)
            def test_cap(self):
                b, now = make(4, 10); b.allow(4); now[0] = 100; self.assertAlmostEqual(b.remaining(), 4.0)
            def test_refill_then_allow(self):
                b, now = make(10, 1); b.allow(10); now[0] = 2; self.assertFalse(b.allow(3)); now[0] = 3
                self.assertTrue(b.allow(3))
            def test_denied_keeps_time_progress(self):
                b, now = make(10, 1); b.allow(10); now[0] = 1; b.allow(5); now[0] = 2
                self.assertAlmostEqual(b.remaining(), 2.0)
            def test_bad_n(self):
                b, _ = make(3, 1)
                for n in (0, -1):
                    with self.assertRaises(ValueError): b.allow(n)
            def test_remaining_full(self):
                b, _ = make(7, 1); self.assertAlmostEqual(b.remaining(), 7.0)
        ''')),
    BenchTask("toposort", "medium-hard: graph + deterministic order",
        "Implement toposort(graph: dict) -> list. graph maps each node to the list of nodes it depends on (they must come "
        "before it). Nodes that only appear as dependencies are included too. Output must be deterministic: at every step, "
        "among all nodes whose dependencies are already placed, output the smallest one (normal < ordering). Duplicate entries "
        "in a dependency list are harmless. A cycle, including a node depending on itself, raises ValueError. The input must "
        "not be modified. Empty graph returns [].",
        "def toposort(graph):\n    raise NotImplementedError\n",
        "import unittest\nfrom solution import toposort\n\nclass T(unittest.TestCase):\n"
        "    def test_chain(self):\n        self.assertEqual(toposort({'a': ['b'], 'b': []}), ['b', 'a'])\n",
        textwrap.dedent('''\
        import unittest
        from solution import toposort

        class H(unittest.TestCase):
            def test_chain(self): self.assertEqual(toposort({"a": ["b"], "b": ["c"], "c": []}), ["c", "b", "a"])
            def test_empty(self): self.assertEqual(toposort({}), [])
            def test_alpha_ties(self): self.assertEqual(toposort({"b": [], "a": []}), ["a", "b"])
            def test_greedy_smallest(self):
                self.assertEqual(toposort({"d": ["a"], "c": ["a"], "b": [], "a": []}), ["a", "b", "c", "d"])
            def test_waits_for_all_deps(self):
                self.assertEqual(toposort({"x": [], "a": [], "m": ["a", "x"]}), ["a", "x", "m"])
            def test_dep_only_nodes(self): self.assertEqual(toposort({"a": ["z"]}), ["z", "a"])
            def test_duplicates(self): self.assertEqual(toposort({"a": ["b", "b"]}), ["b", "a"])
            def test_cycle(self):
                with self.assertRaises(ValueError): toposort({"a": ["b"], "b": ["a"]})
            def test_self_loop(self):
                with self.assertRaises(ValueError): toposort({"a": ["a"]})
            def test_partial_cycle(self):
                with self.assertRaises(ValueError): toposort({"ok": [], "a": ["b"], "b": ["c"], "c": ["a"]})
            def test_no_mutation(self):
                g = {"a": ["b"], "b": []}; toposort(g); self.assertEqual(g, {"a": ["b"], "b": []})
        ''')),
]

PROMPT = ("The current directory has solution.py and test_solution.py.\n{spec}\n"
          "Edit solution.py directly so the spec is met and the tests in test_solution.py pass. Other, stricter tests will be "
          "run too, so follow the spec exactly, including edge cases. Do not create other files and do not ask questions. "
          "If you can run commands, run `python3 -m unittest test_solution`. Finish with the single line: DONE")

_HARNESS = ("import unittest, json, os\nsuite = unittest.defaultTestLoader.loadTestsFromName('test_hidden')\n"
            "res = unittest.TextTestRunner(stream=open(os.devnull, 'w')).run(suite)\n"
            "n = suite.countTestCases(); bad = len(res.failures) + len(res.errors)\n"
            "print(json.dumps({'total': n, 'passed': max(0, n - bad)}))\n")


@dataclass
class TaskScore:
    task: str
    score: float
    seconds: float
    error: str | None = None


@dataclass
class CandidateResult:
    worker: str
    model: str | None
    scores: list[TaskScore] = field(default_factory=list)
    planned: int = 0          # tasks this candidate was meant to run; tasks skipped after early stop count as 0

    @property
    def total(self) -> float:
        return sum(s.score for s in self.scores) / max(self.planned or len(TASKS), 1)

    @property
    def seconds(self) -> float:
        done = [s.seconds for s in self.scores if s.error is None]
        return sum(done) / len(done) if done else 0.0

    @property
    def errors(self) -> list[str]:
        return sorted({s.error for s in self.scores if s.error})

    def to_dict(self) -> dict:
        return {"worker": self.worker, "model": self.model, "score": round(self.total, 3),
                "avg_seconds": round(self.seconds, 1), "errors": self.errors,
                "tasks": [s.__dict__ for s in self.scores]}


def evaluate(workdir: Path, task: BenchTask) -> float:
    (workdir / "test_hidden.py").write_text(task.hidden, encoding="utf-8")
    try:
        r = subprocess.run([sys.executable, "-c", _HARNESS], cwd=workdir, capture_output=True, text=True, timeout=30,
                           env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "HOME": str(workdir)})   # model-written code: no inherited secrets
        data = json.loads(r.stdout.strip().splitlines()[-1])
        return data["passed"] / data["total"] if data["total"] else 0.0
    except (subprocess.TimeoutExpired, json.JSONDecodeError, IndexError, KeyError):
        return 0.0


def run_one(registry: Registry, info: AgentInfo, model: str | None, task: BenchTask, timeout: int) -> TaskScore:
    adapter = registry.get(info.name)
    with tempfile.TemporaryDirectory(prefix="agentmesh-bench-") as tmp:
        wd = Path(tmp)
        (wd / "solution.py").write_text(task.stub, encoding="utf-8")
        (wd / "test_solution.py").write_text(task.visible, encoding="utf-8")
        ctx = RunContext(task=Task(task_id="bench", title=task.id, role="bench"), prompt=PROMPT.format(spec=task.spec),
                         cwd=wd, timeout=timeout, autonomy="full" if info.name in FULL_ONLY else "edit",
                         model=model, scratch=wd)
        t0 = time.monotonic()
        try:
            raw = adapter.execute(adapter.build_command(ctx, info), run_id=f"bench-{info.name}-{task.id}")
            norm = adapter.normalize_result(raw)
            code = adapter.classify_error(raw, norm)
        except Exception as e:                                           # an adapter refusing/crashing is a result, not a crash
            return TaskScore(task.id, 0.0, round(time.monotonic() - t0, 1), type(e).__name__)
        secs = round(time.monotonic() - t0, 1)
        (wd / "solution.py").touch()
        return TaskScore(task.id, evaluate(wd, task), secs, code.value if code else None)


def candidates(registry: Registry, infos: dict[str, AgentInfo], model_lists: dict[str, list[str]],
               only: set[str] | None = None) -> list[tuple[str, str | None]]:
    """(worker, model) pairs to measure. Workers with a model list are measured per model."""
    out: list[tuple[str, str | None]] = []
    for ad in registry.adapters():
        i = infos.get(ad.name)
        if i is None or not i.ready or ad.name in SKIP or isinstance(ad, MockAdapter) or (only and ad.name not in only):
            continue
        models = model_lists.get(ad.name)
        out += [(ad.name, m) for m in models] if models else [(ad.name, None)]
    return out


def run_benchmark(registry: Registry, infos: dict[str, AgentInfo], cands: list[tuple[str, str | None]], *,
                  tasks: list[BenchTask] | None = None, parallel: int = 6, per_worker: int = 2, timeout: int = 240,
                  emit: Callable[[str], None] = lambda m: None) -> list[CandidateResult]:
    tasks = tasks or TASKS
    sems = {w: threading.Semaphore(per_worker) for w, _ in cands}

    def one(c: tuple[str, str | None]) -> CandidateResult:
        worker, model = c
        res = CandidateResult(worker, model, planned=len(tasks))
        with sems[worker]:
            for t in tasks:
                s = run_one(registry, infos[worker], model, t, timeout)
                res.scores.append(s)
                emit(f"  {worker}{'/' + model if model else ''} {t.id}: {s.score:.2f} {s.seconds}s {s.error or ''}")
                if s.error in ("AUTH_FAILED", "QUOTA_EXCEEDED", "CLI_NOT_FOUND", "UNSUPPORTED", "UnsupportedError"):
                    break                                     # cannot serve at all: don't burn the other tasks
                if len(res.scores) == 2 and sum(x.score for x in res.scores) == 0:
                    break                                     # two clean zeros: clearly not usable
        return res

    with ThreadPoolExecutor(max_workers=parallel) as pool:
        return list(pool.map(one, cands))


def ranked(results: list[CandidateResult]) -> list[CandidateResult]:
    return sorted(results, key=lambda r: (-r.total, r.seconds if r.seconds else 1e9))


def saved_path() -> Path:
    return global_home() / "benchmark.json"


def load_saved() -> list[dict]:
    try:
        return json.loads(saved_path().read_text(encoding="utf-8")).get("results", [])
    except (OSError, json.JSONDecodeError):
        return []


def save(results: list[CandidateResult]) -> Path:
    """Merge into the saved file: a re-measured (worker, model) replaces its old entry, others are kept."""
    merged = {(r["worker"], r["model"]): r for r in load_saved()}
    for r in results:
        merged[(r.worker, r.model)] = {**r.to_dict(), "at": utcnow()}
    p = saved_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    rows = sorted(merged.values(), key=lambda r: (-r["score"], r["avg_seconds"] or 1e9))
    p.write_text(json.dumps({"at": utcnow(), "tasks": [t.id for t in TASKS], "results": rows}, indent=2), encoding="utf-8")
    return p


def measured_chain(worker: str, available: list[str], min_score: float = 0.5) -> list[str]:
    """Models of `worker` that this machine has AND that scored >= min_score, best first (ties: faster first)."""
    rows = [r for r in load_saved() if r["worker"] == worker and r["model"] in available and r["score"] >= min_score]
    rows.sort(key=lambda r: (-r["score"], r["avg_seconds"] or 1e9))
    return [r["model"] for r in rows]


def measured_quality_order(min_score: float = 0.5) -> list[str]:
    best: dict[str, dict] = {}
    for r in load_saved():
        if r["score"] >= min_score and (r["worker"] not in best or (-r["score"], r["avg_seconds"]) < (-best[r["worker"]]["score"], best[r["worker"]]["avg_seconds"])):
            best[r["worker"]] = r
    return [w for w, _ in sorted(best.items(), key=lambda kv: (-kv[1]["score"], kv[1]["avg_seconds"]))]


def suggestions(results: list[CandidateResult], min_score: float = 0.5) -> dict:
    """Measured orderings: per-worker model chains and a worker quality order."""
    chains: dict[str, list[str]] = {}
    best: dict[str, CandidateResult] = {}
    for r in ranked(results):
        if r.total < min_score:
            continue
        if r.model:
            chains.setdefault(r.worker, []).append(r.model)
        best.setdefault(r.worker, r)
    return {"model_chains": chains, "quality_order": [w for w, _ in sorted(best.items(), key=lambda kv: (-kv[1].total, kv[1].seconds))]}
