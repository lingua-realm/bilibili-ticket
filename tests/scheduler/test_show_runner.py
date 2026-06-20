from __future__ import annotations

from dataclasses import dataclass

import pytest

from bilibili_ticket.errors import HumanInterventionRequired
from bilibili_ticket.models import OrderResult


@dataclass
class FakeOrderExecutor:
    available_candidates: list[tuple[str, int]]
    results: dict[tuple[str, int], OrderResult]
    raise_for_candidate: tuple[str, int] | None = None
    attempts: list[tuple[str, int]] | None = None

    def list_available_candidates(self) -> list[tuple[str, int]]:
        return list(self.available_candidates)

    def attempt_order(self, candidate: tuple[str, int]) -> OrderResult:
        if self.attempts is not None:
            self.attempts.append(candidate)
        if self.raise_for_candidate == candidate:
            raise HumanInterventionRequired(100044, "captcha")
        return self.results[candidate]


def test_stop_same_show_after_first_lock_success():
    from bilibili_ticket.scheduler.show_runner import ShowRunner

    executor = FakeOrderExecutor(
        available_candidates=[
            ("2026-05-02", 680),
            ("2026-05-01", 480),
        ],
        results={
            ("2026-05-01", 480): OrderResult(success=True, code=0, message="ok", order_id=9527),
            ("2026-05-02", 680): OrderResult(success=True, code=0, message="ok", order_id=9528),
        },
    )
    runner = ShowRunner(
        show_id="bw-2026",
        date_priority=["2026-05-01", "2026-05-02"],
        price_priority=[680, 480],
        available_candidates_provider=executor.list_available_candidates,
        order_executor=executor.attempt_order,
    )

    result = runner.run_once()

    assert result.locked_candidate == ("2026-05-01", 480)
    assert result.stopped_remaining_candidates is True
    assert result.pause_candidate is None
    assert result.order_result.order_id == 9527
    assert runner.state.name == "LOCKED"


def test_pause_show_when_human_intervention_is_required():
    from bilibili_ticket.scheduler.show_runner import ShowRunner

    executor = FakeOrderExecutor(
        available_candidates=[("2026-05-01", 680)],
        results={},
        raise_for_candidate=("2026-05-01", 680),
    )
    runner = ShowRunner(
        show_id="bw-2026",
        date_priority=["2026-05-01"],
        price_priority=[680],
        available_candidates_provider=executor.list_available_candidates,
        order_executor=executor.attempt_order,
    )

    result = runner.run_once()

    assert result.locked_candidate is None
    assert result.pause_candidate == ("2026-05-01", 680)
    assert "100044" in result.pause_reason
    assert runner.state.name == "PAUSED_FOR_HUMAN"


def test_resume_monitoring_when_locked_order_is_released():
    from bilibili_ticket.scheduler.show_runner import ShowRunner

    executor = FakeOrderExecutor(
        available_candidates=[("2026-05-01", 680)],
        results={
            ("2026-05-01", 680): OrderResult(success=True, code=0, message="ok", order_id=9528)
        },
    )
    released_order = OrderResult(success=True, code=0, message="ok", order_id=9527)
    checker_calls = []
    runner = ShowRunner(
        show_id="bw-2026",
        date_priority=["2026-05-01"],
        price_priority=[680],
        available_candidates_provider=executor.list_available_candidates,
        order_executor=executor.attempt_order,
        locked_order_resume_checker=lambda order: checker_calls.append(order.order_id) or True,
    )
    runner.state = runner.state.LOCKED
    runner.last_result = runner.last_result.__class__(
        locked_candidate=("2026-05-01", 680),
        stopped_remaining_candidates=True,
        order_result=released_order,
    )

    result = runner.run_once()

    assert checker_calls == [9527]
    assert result.order_result.order_id == 9528
    assert runner.state.name == "LOCKED"


def test_keep_locked_state_when_order_is_still_payable():
    from bilibili_ticket.scheduler.show_runner import ShowRunner

    executor = FakeOrderExecutor(
        available_candidates=[("2026-05-01", 680)],
        results={
            ("2026-05-01", 680): OrderResult(success=True, code=0, message="ok", order_id=9528)
        },
    )
    runner = ShowRunner(
        show_id="bw-2026",
        date_priority=["2026-05-01"],
        price_priority=[680],
        available_candidates_provider=executor.list_available_candidates,
        order_executor=executor.attempt_order,
        locked_order_resume_checker=lambda order: False,
    )
    runner.state = runner.state.LOCKED
    runner.last_result = runner.last_result.__class__(
        locked_candidate=("2026-05-01", 680),
        stopped_remaining_candidates=True,
        order_result=OrderResult(success=True, code=0, message="ok", order_id=9527),
    )

    result = runner.run_once()

    assert result.locked_candidate == ("2026-05-01", 680)
    assert result.order_result.order_id == 9527
    assert runner.state.name == "LOCKED"


def test_record_failed_attempts_during_polling_round():
    from bilibili_ticket.scheduler.show_runner import ShowRunner

    executor = FakeOrderExecutor(
        available_candidates=[
            ("2026-05-01", 8800),
            ("2026-05-01", 6800),
        ],
        results={
            ("2026-05-01", 8800): OrderResult(success=False, code=100017, message="票种不可售"),
            ("2026-05-01", 6800): OrderResult(success=False, code=900001, message="前方拥堵，请重试."),
        },
    )
    runner = ShowRunner(
        show_id="bw-2026",
        date_priority=["2026-05-01"],
        price_priority=[8800, 6800],
        available_candidates_provider=executor.list_available_candidates,
        order_executor=executor.attempt_order,
    )

    result = runner.run_once()

    assert result.available_candidates == [("2026-05-01", 8800), ("2026-05-01", 6800)]
    assert len(result.attempt_records) == 2
    assert result.attempt_records[0].candidate == ("2026-05-01", 8800)
    assert result.attempt_records[0].code == 100017
    assert result.attempt_records[1].candidate == ("2026-05-01", 6800)
    assert result.attempt_records[1].message == "前方拥堵，请重试."


def test_skip_order_attempt_before_sprint_window():
    from bilibili_ticket.scheduler.show_runner import ShowRunner

    attempts = []
    executor = FakeOrderExecutor(
        available_candidates=[("2026-07-11", 12800)],
        results={
            ("2026-07-11", 12800): OrderResult(success=True, code=0, message="ok", order_id=9527)
        },
        attempts=attempts,
    )
    runner = ShowRunner(
        show_id="bw-2026",
        date_priority=["2026-07-11"],
        price_priority=[12800],
        available_candidates_provider=executor.list_available_candidates,
        order_executor=executor.attempt_order,
        candidate_sale_start_provider=lambda _: 100.0,
        now=lambda: 50.0,
    )

    result = runner.run_once()

    assert attempts == []
    assert result.available_candidates == [("2026-07-11", 12800)]
    assert result.attempt_records == []
    assert result.phase == "PREHEAT"
    assert result.next_delay_seconds == 1.0


def test_attempt_order_inside_sprint_window():
    from bilibili_ticket.scheduler.show_runner import ShowRunner

    attempts = []
    executor = FakeOrderExecutor(
        available_candidates=[("2026-07-11", 12800)],
        results={
            ("2026-07-11", 12800): OrderResult(success=True, code=0, message="ok", order_id=9527)
        },
        attempts=attempts,
    )
    runner = ShowRunner(
        show_id="bw-2026",
        date_priority=["2026-07-11"],
        price_priority=[12800],
        available_candidates_provider=executor.list_available_candidates,
        order_executor=executor.attempt_order,
        candidate_sale_start_provider=lambda _: 100.0,
        now=lambda: 98.0,
    )

    result = runner.run_once()

    assert attempts == [("2026-07-11", 12800)]
    assert result.locked_candidate == ("2026-07-11", 12800)


def test_front_crowd_failure_keeps_runner_active_with_short_backoff():
    from bilibili_ticket.scheduler.show_runner import ShowRunner

    attempts = []
    executor = FakeOrderExecutor(
        available_candidates=[("2026-07-11", 12800)],
        results={
            ("2026-07-11", 12800): OrderResult(
                success=False,
                code=100041,
                message="前方拥堵，请重试...",
            )
        },
        attempts=attempts,
    )
    runner = ShowRunner(
        show_id="bw-2026",
        date_priority=["2026-07-11"],
        price_priority=[12800],
        available_candidates_provider=executor.list_available_candidates,
        order_executor=executor.attempt_order,
        candidate_sale_start_provider=lambda _: 100.0,
        now=lambda: 100.0,
    )

    first = runner.run_once()
    second = runner.run_once()

    assert attempts == [("2026-07-11", 12800), ("2026-07-11", 12800)]
    assert runner.state.name == "RUNNING"
    assert first.next_delay_seconds == 0.3
    assert second.next_delay_seconds == 0.5


def test_http_429_failure_uses_front_crowd_backoff():
    from bilibili_ticket.scheduler.show_runner import ShowRunner

    executor = FakeOrderExecutor(
        available_candidates=[("2026-07-11", 12800)],
        results={
            ("2026-07-11", 12800): OrderResult(
                success=False,
                code=429,
                message="HTTP 429 Too Many Requests",
            )
        },
    )
    runner = ShowRunner(
        show_id="bw-2026",
        date_priority=["2026-07-11"],
        price_priority=[12800],
        available_candidates_provider=executor.list_available_candidates,
        order_executor=executor.attempt_order,
        candidate_sale_start_provider=lambda _: 100.0,
        now=lambda: 100.0,
    )

    first = runner.run_once()
    second = runner.run_once()

    assert first.next_delay_seconds == 0.3
    assert second.next_delay_seconds == 0.5


def test_unknown_sale_start_keeps_default_retry_delay_after_failed_attempt():
    from bilibili_ticket.scheduler.show_runner import ShowRunner

    executor = FakeOrderExecutor(
        available_candidates=[("2026-07-11", 12800)],
        results={
            ("2026-07-11", 12800): OrderResult(
                success=False,
                code=100017,
                message="票种不可售",
            )
        },
    )
    runner = ShowRunner(
        show_id="bw-2026",
        date_priority=["2026-07-11"],
        price_priority=[12800],
        available_candidates_provider=executor.list_available_candidates,
        order_executor=executor.attempt_order,
    )

    result = runner.run_once()

    assert result.next_delay_seconds == 1.0
