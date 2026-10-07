import pytest

from agentmesh.config import ProjectConfig, ProjectPaths, deep_merge, parse_scalar, set_dotted, DEFAULTS
from agentmesh.errors import AgentMeshError
from agentmesh.registry import build_registry


def test_builtin_adapters_and_aliases():
    reg = build_registry(custom={}, mock_env=False)
    assert {"claude", "codex", "antigravity", "qwen", "kilo", "freebuff", "gemini", "opencode", "command-code"} <= set(reg.names())
    assert reg.get("agy").name == "antigravity" and reg.get("cmd").name == "command-code"
    with pytest.raises(AgentMeshError):
        reg.get("nope")


def test_custom_generic_adapter_registers_and_overrides_builtin():
    reg = build_registry(custom={"mytool": {"executable": "mt", "headless_args": ["--go", "{prompt}"]},
                                 "kilo": {"executable": "kilo", "headless_args": ["run", "{prompt}"]}}, mock_env=False)
    assert reg.get("mytool").executables == ("mt",)
    assert not reg.get("kilo").requires_config      # user config replaced the placeholder adapter


def test_mock_env(monkeypatch):
    monkeypatch.setenv("AGENTMESH_MOCK", "a:QUOTA_EXCEEDED,b")
    reg = build_registry(custom={})
    assert reg.get("a").behaviors == ["QUOTA_EXCEEDED"] and reg.get("b").behaviors == ["SUCCESS"]


def test_config_defaults_and_merge(tmp_path):
    cfg = ProjectConfig(ProjectPaths(tmp_path), {"routing": {"strategy": "quality-first"}, "project": {"name": "X", "kind": "web"}})
    assert cfg.get("routing.strategy") == "quality-first"
    assert cfg.get("fallback.enabled") is True and cfg.get("delegation.max_depth") == 1
    assert cfg.get("nope.deep", "d") == "d" and cfg.name == "X"


def test_role_overrides_patch_generated_roles(tmp_path):
    cfg = ProjectConfig(ProjectPaths(tmp_path), {"roles": {"r": {"allowed_paths": ["a/**"], "capability": "write"}},
                                                 "overrides": {"roles": {"r": {"allowed_paths": ["b/**"]}}}})
    assert cfg.roles()["r"]["allowed_paths"] == ["b/**"] and cfg.roles()["r"]["capability"] == "write"


def test_role_autonomy_override(tmp_path):
    cfg = ProjectConfig(ProjectPaths(tmp_path), {"workers": {"role_autonomy": {"test-executor": "full"}}})
    assert cfg.role_autonomy("test-executor") == "full" and cfg.role_autonomy("reviewer") == "edit"


def test_helpers():
    d = {}
    set_dotted(d, "a.b.c", 1)
    assert d == {"a": {"b": {"c": 1}}}
    assert parse_scalar("true") is True and parse_scalar('["x"]') == ["x"] and parse_scalar("abc") == "abc"
    assert deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 9}}) == {"a": {"b": 9, "c": 2}}
    assert DEFAULTS["delegation"]["max_depth"] == 1       # defaults never mutated
