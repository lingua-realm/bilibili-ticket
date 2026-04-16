from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from bilibili_ticket.models import ShowTaskConfig


@dataclass(slots=True)
class RunnerSnapshot:
    state: str


class ScheduleManager:
    def __init__(
        self,
        configs: list[ShowTaskConfig],
        session_snapshot: dict[str, str],
        runner_factory: Callable[[ShowTaskConfig, dict[str, str]], object],
    ):
        self.configs = configs
        self.session_snapshot = session_snapshot
        self.runner_factory = runner_factory
        self.runners: dict[str, object] = {}

    def create_runners(self) -> dict[str, object]:
        if self.runners:
            return self.runners
        for config in self.configs:
            self.runners[config.show_id] = self.runner_factory(
                config,
                dict(self.session_snapshot),
            )
        return self.runners

    def run_iteration(self) -> dict[str, RunnerSnapshot]:
        self.create_runners()
        snapshots: dict[str, RunnerSnapshot] = {}
        for show_id, runner in self.runners.items():
            runner.run_once()
            snapshots[show_id] = RunnerSnapshot(state=runner.state.name)
        return snapshots
