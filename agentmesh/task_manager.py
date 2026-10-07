"""Task + report persistence under .agentmesh/tasks and .agentmesh/reports."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import ProjectPaths
from .errors import ProjectError
from .models import Task, WorkerResult


class TaskManager:
    def __init__(self, paths: ProjectPaths):
        self.paths = paths

    def next_id(self) -> str:
        self.paths.tasks_dir.mkdir(parents=True, exist_ok=True)
        nums = [int(m.group(1)) for p in self.paths.tasks_dir.glob("task-*.json")
                if (m := re.match(r"task-(\d+)\.json$", p.name))]
        return f"task-{(max(nums) + 1 if nums else 1):03d}"

    def _task_path(self, task_id: str) -> Path:
        return self.paths.tasks_dir / f"{task_id}.json"

    def save(self, task: Task) -> None:
        self.paths.tasks_dir.mkdir(parents=True, exist_ok=True)
        self._task_path(task.task_id).write_text(json.dumps(task.to_dict(), indent=2), encoding="utf-8")

    def load(self, task_id: str) -> Task:
        p = self._task_path(task_id)
        if not p.is_file():
            raise ProjectError(f"unknown task '{task_id}'")
        return Task.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def list(self) -> list[Task]:
        if not self.paths.tasks_dir.is_dir():
            return []
        return [Task.from_dict(json.loads(p.read_text(encoding="utf-8")))
                for p in sorted(self.paths.tasks_dir.glob("task-*.json"))]

    def save_result(self, result: WorkerResult) -> None:
        self.paths.reports_dir.mkdir(parents=True, exist_ok=True)
        data = json.dumps(result.to_dict(), indent=2)
        (self.paths.reports_dir / f"{result.task_id}.json").write_text(data, encoding="utf-8")
        with (self.paths.reports_dir / f"{result.task_id}.history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(result.to_dict()) + "\n")

    def load_result(self, task_id: str) -> WorkerResult | None:
        p = self.paths.reports_dir / f"{task_id}.json"
        if not p.is_file():
            return None
        return WorkerResult.from_dict(json.loads(p.read_text(encoding="utf-8")))
