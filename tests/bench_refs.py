"""Reference solutions for the harder benchmark tasks (used by test_benchmark)."""

HARD = {
"expr": '''
import re

_TOK = re.compile(r"\\s*(?:(\\d+\\.?\\d*|\\.\\d+)|(\\*\\*|[-+*/()])|(\\S))")


def evaluate(expr):
    toks, pos = [], 0
    while pos < len(expr) and expr[pos:].strip():
        m = _TOK.match(expr, pos)
        if m.group(3):
            raise ValueError("bad char")
        toks.append(("num", float(m.group(1))) if m.group(1) else ("op", m.group(2)))
        pos = m.end()
    if not toks:
        raise ValueError("empty")
    i = 0

    def peek():
        return toks[i] if i < len(toks) else (None, None)

    def take():
        nonlocal i
        i += 1
        return toks[i - 1]

    def add():
        v = mul()
        while peek() in (("op", "+"), ("op", "-")):
            o = take()[1]
            r = mul()
            v = v + r if o == "+" else v - r
        return v

    def mul():
        v = unary()
        while peek() in (("op", "*"), ("op", "/")):
            o = take()[1]
            r = unary()
            v = v * r if o == "*" else v / r
        return v

    def unary():
        if peek() in (("op", "-"), ("op", "+")):
            return -unary() if take()[1] == "-" else unary()
        return power()

    def power():
        b = atom()
        if peek() == ("op", "**"):
            take()
            return b ** unary()
        return b

    def atom():
        k, v = peek()
        if k == "num":
            take()
            return v
        if (k, v) == ("op", "("):
            take()
            r = add()
            if peek() != ("op", ")"):
                raise ValueError("unbalanced")
            take()
            return r
        raise ValueError("unexpected")

    r = add()
    if i != len(toks):
        raise ValueError("trailing")
    return r
''',
"bucket": '''
import time


class TokenBucket:
    def __init__(self, capacity, rate, clock=time.monotonic):
        self.capacity, self.rate, self.clock = capacity, rate, clock
        self.tokens = float(capacity)
        self.last = clock()

    def _refill(self):
        now = self.clock()
        self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
        self.last = now

    def allow(self, n=1):
        if n <= 0:
            raise ValueError("n")
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def remaining(self):
        self._refill()
        return float(self.tokens)
''',
"toposort": '''
import heapq


def toposort(graph):
    deps = {n: set(d) for n, d in graph.items()}
    for d in list(deps.values()):
        for x in d:
            deps.setdefault(x, set())
    users = {n: [] for n in deps}
    for n, d in deps.items():
        for x in d:
            users[x].append(n)
    left = {n: len(d) for n, d in deps.items()}
    heap = [n for n, c in left.items() if c == 0]
    heapq.heapify(heap)
    out = []
    while heap:
        n = heapq.heappop(heap)
        out.append(n)
        for u in users[n]:
            left[u] -= 1
            if left[u] == 0:
                heapq.heappush(heap, u)
    if len(out) != len(deps):
        raise ValueError("cycle")
    return out
''',
}
