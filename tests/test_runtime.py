from __future__ import annotations

from dataclasses import dataclass

import httpx

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
    def __init__(self, runners, failures=None):
        self.runners = runners
        self.run_count = 0
        self.failures = list(failures or [])

    def run_iteration(self):
        self.run_count += 1
        if self.failures:
            failure = self.failures.pop(0)
            if isinstance(failure, BaseException):
                raise failure
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
            order_result=OrderResult(
                success=True,
                code=0,
                message="ok",
                order_id=9527,
                order_url="https://show.bilibili.com/platform/orderDetail.html?order_id=9527",
                pay_money=20400,
                pay_remain_seconds=600,
                buyer_summary="张*、李*",
                ticket_name="早鸟票",
            ),
        ),
    )
    manager = FakeManager({"bw-2026": runner})
    notifier = FakeNotifier()

    exit_code = run_scheduler(manager=manager, notifier=notifier, once=True, sleep=lambda _: None)

    assert exit_code == 0
    assert len(notifier.lock_events) == 1
    assert notifier.lock_events[0].order_id == 9527
    assert notifier.lock_events[0].order_url == "https://show.bilibili.com/platform/orderDetail.html?order_id=9527"
    assert notifier.lock_events[0].pay_money == 20400
    assert notifier.lock_events[0].pay_remain_seconds == 600


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


def test_run_scheduler_emits_iteration_status_lines():
    from bilibili_ticket.runtime import run_scheduler
    from bilibili_ticket.scheduler.show_runner import AttemptRecord

    runner = FakeRunner(
        state=FakeState("RUNNING"),
        last_result=ShowRunResult(
            locked_candidate=None,
            stopped_remaining_candidates=False,
            available_candidates=[("2026-05-01", 8800), ("2026-05-01", 6800)],
            attempt_records=[
                AttemptRecord(
                    candidate=("2026-05-01", 8800),
                    success=False,
                    code=100017,
                    message="票种不可售",
                ),
                AttemptRecord(
                    candidate=("2026-05-01", 6800),
                    success=False,
                    code=900001,
                    message="前方拥堵，请重试.",
                ),
            ],
        ),
        display_name="真实演出标题",
    )
    manager = FakeManager({"internal-show-id": runner})
    notifier = FakeNotifier()
    lines = []

    exit_code = run_scheduler(
        manager=manager,
        notifier=notifier,
        once=True,
        sleep=lambda _: None,
        status_writer=lines.extend,
    )

    assert exit_code == 0
    assert len(lines) == 1
    assert "真实演出标题" in lines[0]
    assert "状态=RUNNING" in lines[0]
    assert "2026-05-01/88.00元" in lines[0]
    assert "100017" in lines[0]
    assert "900001" in lines[0]


def test_run_scheduler_uses_runner_suggested_next_delay():
    from bilibili_ticket.runtime import run_scheduler

    runner = FakeRunner(
        state=FakeState("RUNNING"),
        last_result=ShowRunResult(
            locked_candidate=None,
            stopped_remaining_candidates=False,
            next_delay_seconds=0.3,
        ),
    )
    manager = FakeManager({"bw-2026": runner})
    notifier = FakeNotifier()
    sleeps = []

    def stop_after_sleep(delay):
        sleeps.append(delay)
        raise KeyboardInterrupt

    exit_code = run_scheduler(
        manager=manager,
        notifier=notifier,
        once=False,
        interval=1.0,
        sleep=stop_after_sleep,
    )

    assert exit_code == 130
    assert sleeps == [0.3]


def test_run_scheduler_retries_after_http_429():
    from bilibili_ticket.runtime import run_scheduler

    request = httpx.Request("GET", "https://show.bilibili.com/api/ticket/project/getV2")
    response = httpx.Response(429, request=request)
    manager = FakeManager(
        {},
        failures=[
            httpx.HTTPStatusError(
                "Client error '429 Too Many Requests'",
                request=request,
                response=response,
            ),
            KeyboardInterrupt(),
        ],
    )
    notifier = FakeNotifier()
    sleeps = []

    exit_code = run_scheduler(
        manager=manager,
        notifier=notifier,
        once=False,
        interval=1.0,
        sleep=sleeps.append,
    )

    assert exit_code == 130
    assert manager.run_count == 2
    assert sleeps == [1.0]
