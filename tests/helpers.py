from __future__ import annotations

from pathlib import Path

from agentmesh import discovery
from agentmesh.adapters import MockAdapter
from agentmesh.config import ProjectConfig
from agentmesh.initializer import init_project
from agentmesh.models import Task
from agentmesh.registry import Registry
from agentmesh.runner import Runner
from agentmesh.state import RuntimeState
from agentmesh.task_manager import TaskManager


def mock_registry(spec: dict[str, str | list[str]], **kw) -> Registry:
    reg = Registry()
    for name, beh in spec.items():
        reg.register(MockAdapter(name, beh, **kw.get(name, {})))
    return reg


def init_with(root: Path, reg: Registry, **kw):
    kw.setdefault("path_env", "")
    return init_project(root, reg, **kw)


def make_runner(root: Path, reg: Registry, settings: dict | None = None):
    """init the project (mock workers only), optionally patch project.yaml sections, return (runner, cfg)."""
    from agentmesh.config import dump_yaml, load_yaml
    init_with(root, reg)
    if settings:
        p = root / ".agentmesh" / "project.yaml"
        data = load_yaml(p)
        from agentmesh.config import deep_merge
        p.write_text(dump_yaml(deep_merge(data, settings)))
    cfg = ProjectConfig.load(root)
    infos = discovery.current(reg).by_name()
    state = RuntimeState(cfg.paths.state_dir / "workers.json")
    return Runner(cfg, reg, infos, state), cfg


def new_task(cfg: ProjectConfig, role: str = "app-engineer", **kw) -> Task:
    spec = cfg.roles()[role]
    tm = TaskManager(cfg.paths)
    t = Task(task_id=tm.next_id(), title=kw.pop("title", "t"), role=role, capability=spec["capability"],
             project_root=str(cfg.paths.root), allowed_paths=kw.pop("allowed_paths", spec["allowed_paths"]), forbidden_paths=spec["forbidden_paths"],
             isolation=kw.pop("isolation", "worktree" if spec["capability"] == "write" else "inplace"), **kw)
    tm.save(t)
    return t
