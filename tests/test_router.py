from agentmesh.adapters import MockAdapter
from agentmesh.config import ProjectConfig, ProjectPaths
from agentmesh.registry import Registry
from agentmesh.router import Router
from agentmesh.state import RuntimeState


def setup(tmp_path, names=("a", "b", "c"), cfg=None, states=None):
    reg = Registry()
    infos = {}
    for n in names:
        ad = MockAdapter(n)
        reg.register(ad)
        infos[n] = ad.health_check()
    for n, st in (states or {}).items():
        infos[n].state = st
    clock = [1000.0]
    state = RuntimeState(tmp_path / "s.json", clock=lambda: clock[0])
    config = ProjectConfig(ProjectPaths(tmp_path), cfg or {})
    return Router(reg, infos, config, state), state, clock


def test_preferred_order(tmp_path):
    r, *_ = setup(tmp_path, cfg={"routing": {"strategy": "preferred-order", "preferred_order": ["c", "a", "b"]}})
    assert r.rank("x").candidates == ["c", "a", "b"]


def test_quality_first_uses_configured_quality_order(tmp_path):
    r, *_ = setup(tmp_path, cfg={"routing": {"strategy": "quality-first", "quality_order": ["b", "c", "a"]}})
    assert r.rank("x").candidates == ["b", "c", "a"]


def test_free_first_puts_declared_free_workers_first(tmp_path):
    r, *_ = setup(tmp_path, cfg={"routing": {"strategy": "free-first", "free_workers": ["c"], "preferred_order": ["a", "b", "c"]}})
    assert r.rank("x").candidates == ["c", "a", "b"]


def test_balanced_rotates_by_usage(tmp_path):
    r, state, _ = setup(tmp_path, cfg={"routing": {"strategy": "balanced", "preferred_order": ["a", "b", "c"]}})
    first = r.rank("x").candidates[0]
    state.record_use(first)
    assert r.rank("x").candidates[0] != first


def test_role_routing_and_preferred_worker_lead(tmp_path):
    r, *_ = setup(tmp_path, cfg={"routing": {"strategy": "preferred-order", "preferred_order": ["a", "b", "c"],
                                             "roles": {"backend-engineer": ["c", "b"]}}})
    assert r.rank("backend-engineer").candidates == ["c", "b", "a"]
    assert r.rank("backend-engineer", preferred="a").candidates[0] == "a"
    assert r.rank("reviewer").candidates == ["a", "b", "c"]          # other roles unaffected


def test_cooldown_excludes_then_expires(tmp_path):
    r, state, clock = setup(tmp_path, cfg={"routing": {"strategy": "preferred-order", "preferred_order": ["a", "b", "c"]}})
    state.record_failure("a", "QUOTA_EXCEEDED", 600)
    rk = r.rank("x")
    assert rk.candidates == ["b", "c"] and "QUOTA_EXCEEDED" in rk.skipped["a"]
    clock[0] += 601
    assert r.rank("x").candidates[0] == "a"


def test_unverified_interactive_and_disabled_are_skipped(tmp_path):
    r, *_ = setup(tmp_path, cfg={"routing": {"disabled": ["c"]}}, states={"a": "UNVERIFIED", "b": "INTERACTIVE_ONLY"})
    rk = r.rank("x")
    assert rk.candidates == [] and set(rk.skipped) == {"a", "b", "c"}


def test_allow_unverified_opt_in(tmp_path):
    r, *_ = setup(tmp_path, names=("a",), cfg={"routing": {"allow_unverified": True}}, states={"a": "UNVERIFIED"})
    assert r.rank("x").candidates == ["a"]


def test_avoid_is_soft(tmp_path):
    r, *_ = setup(tmp_path, cfg={"routing": {"strategy": "preferred-order", "preferred_order": ["a", "b", "c"]}})
    assert r.rank("reviewer", avoid={"a"}).candidates == ["b", "c", "a"]
    r2, *_ = setup(tmp_path, names=("a",))
    assert r2.rank("reviewer", avoid={"a"}).candidates == ["a"]       # only worker: still usable


def test_exclude_already_tried(tmp_path):
    r, *_ = setup(tmp_path)
    assert "a" not in r.rank("x", exclude={"a"}).candidates


def test_runtime_status_never_invents_quota(tmp_path):
    _, state, _ = setup(tmp_path)
    assert state.status("a") == "OK"
    state.record_failure("a", "RATE_LIMITED", 300)
    assert state.status("a") == "RATE_LIMITED"
    state.record_failure("b", "WORKER_FAILED", 0)
    assert state.status("b") == "RECENT_FAILURE"
