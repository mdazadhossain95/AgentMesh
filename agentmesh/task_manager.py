"""Task + report persistence under .agentmesh/tasks and .agentmesh/reports."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .config import ProjectPaths
from .errors import ProjectError
from .models import Task, WorkerResult


class TaskManager:
    def __init__(self, paths: ProjectPaths):
        self.paths = paths

    def next_id(self) -> str:
        """Reserve the next id atomically (O_EXCL), so parallel callers never get the same one."""
        self.paths.tasks_dir.mkdir(parents=True, exist_ok=True)
        while True:
            nums = [int(m.group(1)) for p in self.paths.tasks_dir.glob("task-*.json")
                    if (m := re.match(r"task-(\d+)\.json$", p.name))]
            cand = f"task-{(max(nums) + 1 if nums else 1):03d}"
            try:
                os.close(os.open(self._task_path(cand), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return cand
            except FileExistsError:
                continue

    def _task_path(self, task_id: str) -> Path:
        return self.paths.tasks_dir / f"{task_id}.json"

    def save(self, task: Task) -> None:
        self.paths.tasks_dir.mkdir(parents=True, exist_ok=True)
        path = self._task_path(task.task_id)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(task.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(path)                   # atomic: parallel readers never see a partial file

    def load(self, task_id: str) -> Task:
        p = self._task_path(task_id)
        if not p.is_file():
            raise ProjectError(f"unknown task '{task_id}'")
        return Task.from_dict(json.loads(p.read_text(encoding="utf-8")))

    def list(self) -> list[Task]:
        if not self.paths.tasks_dir.is_dir():
            return []
        out = []
        for p in sorted(self.paths.tasks_dir.glob("task-*.json")):
            text = p.read_text(encoding="utf-8").strip()
            if text:                      # empty file = id reserved but task not saved yet
                out.append(Task.from_dict(json.loads(text)))
        return out

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
