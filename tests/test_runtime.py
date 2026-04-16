from __future__ import annotations

from dataclasses import dataclass

from bilibili_ticket.models import OrderResult
from bilibili_ticket.scheduler.show_runner import ShowRunResult


@dataclass
class FakeState:
    name: str


@dataclass
class FakeRunner:
    state: FakeState
    last_result: ShowRunResult
    display_name: str | None = None


class FakeManager:
    def __init__(self, runners):
        self.runners = runners
        self.run_count = 0

    def run_iteration(self):
        self.run_count += 1
        return {}


class FakeNotifier:
    def __init__(self):
        self.lock_events = []
        self.human_events = []

    def send_lock_success(self, event):
        self.lock_events.append(event)

    def send_human_takeover(self, event):
        self.human_events.append(event)


def test_run_scheduler_sends_lock_success_once():
    from bilibili_ticket.runtime import run_scheduler

    runner = FakeRunner(
        state=FakeState("LOCKED"),
        last_result=ShowRunResult(
            locked_candidate=("2026-05-01", 680),
            stopped_remaining_candidates=True,
            order_result=OrderResult(success=True, code=0, message="ok", order_id=9527),
        ),
    )
    manager = FakeManager({"bw-2026": runner})
    notifier = FakeNotifier()

    exit_code = run_scheduler(manager=manager, notifier=notifier, once=True, sleep=lambda _: None)

    assert exit_code == 0
    assert len(notifier.lock_events) == 1
    assert notifier.lock_events[0].order_id == 9527


def test_run_scheduler_sends_human_takeover_once():
    from bilibili_ticket.runtime import run_scheduler

    runner = FakeRunner(
        state=FakeState("PAUSED_FOR_HUMAN"),
        last_result=ShowRunResult(
            locked_candidate=None,
            stopped_remaining_candidates=False,
            pause_candidate=("2026-05-01", 680),
            pause_reason="[100044] captcha",
        ),
    )
    manager = FakeManager({"bw-2026": runner})
    notifier = FakeNotifier()

    exit_code = run_scheduler(manager=manager, notifier=notifier, once=True, sleep=lambda _: None)

    assert exit_code == 0
    assert len(notifier.human_events) == 1
    assert "100044" in notifier.human_events[0].reason


def test_run_scheduler_prefers_runner_display_name_for_notifications():
    from bilibili_ticket.runtime import run_scheduler

    runner = FakeRunner(
        state=FakeState("PAUSED_FOR_HUMAN"),
        last_result=ShowRunResult(
            locked_candidate=None,
            stopped_remaining_candidates=False,
            pause_candidate=("2026-05-01", 680),
            pause_reason="[100044] captcha",
        ),
        display_name="真实演出标题",
    )
    manager = FakeManager({"internal-show-id": runner})
    notifier = FakeNotifier()

    exit_code = run_scheduler(manager=manager, notifier=notifier, once=True, sleep=lambda _: None)

    assert exit_code == 0
    assert notifier.human_events[0].show_id == "真实演出标题"
