"""Polling watcher; only new/stable files after startup are considered."""

from __future__ import annotations

import math
from pathlib import Path

from fileflow.models import Settings
from fileflow.planner import scan
from fileflow.safety import root_path


class Watcher:
    def __init__(self, root: str | Path, settings: Settings, settle_seconds: float = 2):
        if not math.isfinite(settle_seconds) or settle_seconds < 0 or settings.group_by == "none":
            raise ValueError(
                "Watcher requires an organized destination and a nonnegative settle time"
            )
        self.root, self.settings, self.settle_seconds = root_path(root), settings, settle_seconds
        self.seen = {}
        for path in scan(self.root, settings)[0]:
            stat = path.stat()
            self.seen[path.relative_to(self.root).as_posix()] = (
                stat.st_size,
                stat.st_mtime_ns,
                stat.st_dev,
                stat.st_ino,
            )
        self.pending: dict[str, tuple[tuple[int, int, int, int], float]] = {}

    def poll(self, monotonic_time: float) -> set[str]:
        ready, present = set(), set()
        for path in scan(self.root, self.settings)[0]:
            name = path.relative_to(self.root).as_posix()
            present.add(name)
            stat = path.stat()
            signature = (stat.st_size, stat.st_mtime_ns, stat.st_dev, stat.st_ino)
            if self.seen.get(name) == signature:
                continue
            previous = self.pending.get(name)
            if previous is None or previous[0] != signature:
                self.pending[name] = (signature, monotonic_time)
            elif monotonic_time - previous[1] >= self.settle_seconds:
                ready.add(name)
        self.pending = {name: record for name, record in self.pending.items() if name in present}
        self.seen = {name: record for name, record in self.seen.items() if name in present}
        return ready

    def acknowledge(self, paths: set[str]) -> None:
        for path in paths:
            if path in self.pending:
                self.seen[path] = self.pending.pop(path)[0]
