import pytest

from helpers import make_runner, mock_registry, new_task
from agentmesh.errors import parse_retry_seconds

PREF = {"routing": {"strategy": "preferred-order", "preferred_order": ["w1", "w2"]}}


@pytest.mark.parametrize("text,secs", [
    ("You have hit your usage limit. Try again in 2 hours.", 7200),
    ("rate limit: retry after 45 seconds", 45),
    ("Retry-After: 120", 120),
    ("quota resets in 20 minutes", 1200),
    ("try again in 3 days", 3 * 86400),
    ("try again in 1 second", 30),               # clamped to a sane minimum
    ("try again in 99 days", 7 * 86400),         # and maximum
    ("temporarily rate-limited upstream. Please retry shortly", None),
    ("segfault", None),
])
def test_parse_retry_seconds(text, secs):
    assert parse_retry_seconds(text) == secs


def test_cooldown_follows_the_providers_reset_time(flutter_only):
    reg = mock_registry({"w1": "QUOTA_EXCEEDED", "w2": "SUCCESS"},
                        w1={"messages": {"QUOTA_EXCEEDED": "usage limit reached, try again in 2 hours"}})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    runner.run(new_task(cfg))
    assert 7100 < runner.state.cooldown_remaining("w1") <= 7200            # not the 3600s default


def test_no_duration_in_message_uses_configured_default(flutter_only):
    reg = mock_registry({"w1": "RATE_LIMITED", "w2": "SUCCESS"})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    runner.run(new_task(cfg))
    assert 250 < runner.state.cooldown_remaining("w1") <= 300


def test_worker_returns_automatically_when_limit_ends(flutter_only):
    reg = mock_registry({"w1": ["QUOTA_EXCEEDED", "SUCCESS"], "w2": "SUCCESS"},
                        w1={"messages": {"QUOTA_EXCEEDED": "limit reached, try again in 10 minutes"}, "writes": {"lib/a.dart": "a"}},
                        w2={"writes": {"lib/b.dart": "b"}})
    runner, cfg = make_runner(flutter_only, reg, PREF)
    clock = [1000.0]
    runner.state.clock = lambda: clock[0]
    assert runner.run(new_task(cfg)).worker == "w2"          # w1 hit its limit -> work moved to w2
    assert runner.run(new_task(cfg)).worker == "w2"          # still cooling
    clock[0] += 601
    r = runner.run(new_task(cfg))
    assert r.worker == "w1" and r.status == "SUCCESS"        # limit over -> w1 is used again


def test_model_limit_is_tracked_per_model(flutter_only):
    reg = mock_registry({"w1": ["RATE_LIMITED", "SUCCESS", "SUCCESS"], "w2": "SUCCESS"},
                        w1={"messages": {"RATE_LIMITED": "rate limit, retry after 600"}})
    runner, cfg = make_runner(flutter_only, reg, {**PREF, "workers": {"models": {"w1": ["free-a", "free-b"]}}})
    runner.run(new_task(cfg))
    cooling = runner.state.cooling()
    assert "w1::free-a" in cooling and 550 < cooling["w1::free-a"] <= 600 and "w1" not in cooling
