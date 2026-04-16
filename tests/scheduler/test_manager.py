from __future__ import annotations

from dataclasses import dataclass

from bilibili_ticket.models import ShowTaskConfig


@dataclass
class FakeRunner:
    show_id: str
    state_name_after_run: str
    client: object

    class State:
        def __init__(self, name: str):
            self.name = name

    def __post_init__(self):
        self.state = self.State("RUNNING")

    def run_once(self):
        self.state = self.State(self.state_name_after_run)
        return None


def test_keep_other_shows_running_after_one_show_locks():
    from bilibili_ticket.scheduler.manager import ScheduleManager

    configs = [
        ShowTaskConfig("bw-day1", 1, ["2026-05-01"], [680], [680]),
        ShowTaskConfig("other-show", 2, ["2026-06-01"], [480], [480]),
    ]

    def factory(show_config, session_snapshot):
        state_name = "LOCKED" if show_config.show_id == "bw-day1" else "RUNNING"
        return FakeRunner(show_id=show_config.show_id, state_name_after_run=state_name, client=object())

    manager = ScheduleManager(configs=configs, session_snapshot={"SESSDATA": "abc"}, runner_factory=factory)

    result = manager.run_iteration()

    assert result["bw-day1"].state == "LOCKED"
    assert result["other-show"].state == "RUNNING"


def test_create_separate_runners_from_same_session_snapshot():
    from bilibili_ticket.scheduler.manager import ScheduleManager

    configs = [
        ShowTaskConfig("show-a", 1, ["2026-05-01"], [680], [680]),
        ShowTaskConfig("show-b", 2, ["2026-05-02"], [480], [480]),
    ]
    captured_snapshots = []

    def factory(show_config, session_snapshot):
        captured_snapshots.append(session_snapshot)
        return FakeRunner(show_id=show_config.show_id, state_name_after_run="RUNNING", client=object())

    manager = ScheduleManager(configs=configs, session_snapshot={"SESSDATA": "abc"}, runner_factory=factory)

    manager.create_runners()

    runners = manager.runners
    assert runners["show-a"].client is not runners["show-b"].client
    assert captured_snapshots[0] is not captured_snapshots[1]
    assert captured_snapshots[0] == captured_snapshots[1]
