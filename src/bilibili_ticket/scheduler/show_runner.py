from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

from bilibili_ticket.errors import HumanInterventionRequired
from bilibili_ticket.models import OrderResult
from bilibili_ticket.scheduler.priority import prioritize_available_candidates

PREHEAT_WINDOW_SECONDS = 60.0
SPRINT_WINDOW_SECONDS = 3.0
LOW_FREQUENCY_DELAY_SECONDS = 30.0
PREHEAT_DELAY_SECONDS = 1.0
SPRINT_DELAY_SECONDS = 0.25
FRONT_CROWD_BACKOFF_SECONDS = (0.3, 0.5, 0.8)
FRONT_CROWD_BUSINESS_CODES = {100041, 900001}
RETRYABLE_HTTP_STATUS_CODES = {429}


class ShowState(Enum):
    RUNNING = auto()
    LOCKED = auto()
    PAUSED_FOR_HUMAN = auto()


@dataclass(slots=True)
class ShowRunResult:
    locked_candidate: tuple[str, int] | None
    stopped_remaining_candidates: bool
    pause_candidate: tuple[str, int] | None = None
    pause_reason: str | None = None
    order_result: OrderResult | None = None
    available_candidates: list[tuple[str, int]] = field(default_factory=list)
    attempt_records: list["AttemptRecord"] = field(default_factory=list)
    phase: str | None = None
    seconds_until_sale: float | None = None
    next_delay_seconds: float | None = None


@dataclass(slots=True)
class AttemptRecord:
    candidate: tuple[str, int]
    success: bool
    code: int
    message: str


class ShowRunner:
    def __init__(
        self,
        show_id: str,
        date_priority: list[str],
        price_priority: list[int],
        available_candidates_provider: Callable[[], list[tuple[str, int]]],
        order_executor: Callable[[tuple[str, int]], OrderResult],
        locked_order_resume_checker: Callable[[OrderResult], bool] | None = None,
        candidate_sale_start_provider: Callable[[tuple[str, int]], float | None] | None = None,
        now: Callable[[], float] | None = None,
    ):
        self.show_id = show_id
        self.date_priority = date_priority
        self.price_priority = price_priority
        self.available_candidates_provider = available_candidates_provider
        self.order_executor = order_executor
        self.locked_order_resume_checker = locked_order_resume_checker
        self.candidate_sale_start_provider = candidate_sale_start_provider
        self.now = now or time.time
        self.front_crowd_failures: dict[tuple[str, int], int] = {}
        self.state = ShowState.RUNNING
        self.last_result = ShowRunResult(locked_candidate=None, stopped_remaining_candidates=False)

    def run_once(self) -> ShowRunResult:
        if self.state == ShowState.PAUSED_FOR_HUMAN:
            return self.last_result
        if self.state == ShowState.LOCKED:
            if not self._should_resume_after_lock():
                return self.last_result
            self.state = ShowState.RUNNING

        available_candidates = self.available_candidates_provider()
        prioritized = prioritize_available_candidates(
            available_candidates=available_candidates,
            date_priority=self.date_priority,
            price_priority=self.price_priority,
        )
        attempt_records: list[AttemptRecord] = []
        phase: str | None = None
        seconds_until_sale: float | None = None
        next_delay_seconds: float | None = None
        for candidate in prioritized:
            readiness = self._candidate_readiness(candidate)
            phase = phase or readiness.phase
            if seconds_until_sale is None:
                seconds_until_sale = readiness.seconds_until_sale
            if not readiness.should_attempt:
                next_delay_seconds = self._shorter_delay(
                    next_delay_seconds,
                    readiness.next_delay_seconds,
                )
                continue
            try:
                result = self.order_executor(candidate)
            except HumanInterventionRequired as exc:
                attempt_records.append(
                    AttemptRecord(
                        candidate=candidate,
                        success=False,
                        code=exc.code,
                        message=exc.message,
                    )
                )
                self.state = ShowState.PAUSED_FOR_HUMAN
                self.last_result = ShowRunResult(
                    locked_candidate=None,
                    stopped_remaining_candidates=False,
                    pause_candidate=candidate,
                    pause_reason=str(exc),
                    available_candidates=prioritized,
                    attempt_records=attempt_records,
                    phase=phase,
                    seconds_until_sale=seconds_until_sale,
                )
                return self.last_result
            attempt_records.append(
                AttemptRecord(
                    candidate=candidate,
                    success=result.success,
                    code=result.code,
                    message=result.message,
                )
            )
            if result.success:
                self.state = ShowState.LOCKED
                self.last_result = ShowRunResult(
                    locked_candidate=candidate,
                    stopped_remaining_candidates=True,
                    order_result=result,
                    available_candidates=prioritized,
                    attempt_records=attempt_records,
                )
                return self.last_result
            next_delay_seconds = self._shorter_delay(
                next_delay_seconds,
                self._delay_after_attempt(candidate, result, readiness.phase),
            )
        self.last_result = ShowRunResult(
            locked_candidate=None,
            stopped_remaining_candidates=False,
            available_candidates=prioritized,
            attempt_records=attempt_records,
            phase=phase,
            seconds_until_sale=seconds_until_sale,
            next_delay_seconds=next_delay_seconds,
        )
        return self.last_result

    def _should_resume_after_lock(self) -> bool:
        if self.locked_order_resume_checker is None:
            return False
        order_result = self.last_result.order_result
        if order_result is None or order_result.order_id is None:
            return False
        return self.locked_order_resume_checker(order_result)

    def _candidate_readiness(self, candidate: tuple[str, int]) -> "_CandidateReadiness":
        sale_start = self._candidate_sale_start(candidate)
        if sale_start is None:
            return _CandidateReadiness(
                should_attempt=True,
                phase="READY",
                seconds_until_sale=None,
                next_delay_seconds=None,
            )
        seconds_until_sale = sale_start - self.now()
        if seconds_until_sale > PREHEAT_WINDOW_SECONDS:
            return _CandidateReadiness(
                should_attempt=False,
                phase="WAITING",
                seconds_until_sale=seconds_until_sale,
                next_delay_seconds=min(
                    LOW_FREQUENCY_DELAY_SECONDS,
                    max(seconds_until_sale - PREHEAT_WINDOW_SECONDS, PREHEAT_DELAY_SECONDS),
                ),
            )
        if seconds_until_sale > SPRINT_WINDOW_SECONDS:
            return _CandidateReadiness(
                should_attempt=False,
                phase="PREHEAT",
                seconds_until_sale=seconds_until_sale,
                next_delay_seconds=PREHEAT_DELAY_SECONDS,
            )
        if seconds_until_sale > 0:
            return _CandidateReadiness(
                should_attempt=True,
                phase="SPRINT",
                seconds_until_sale=seconds_until_sale,
                next_delay_seconds=SPRINT_DELAY_SECONDS,
            )
        return _CandidateReadiness(
            should_attempt=True,
            phase="OPEN",
            seconds_until_sale=seconds_until_sale,
            next_delay_seconds=SPRINT_DELAY_SECONDS,
        )

    def _candidate_sale_start(self, candidate: tuple[str, int]) -> float | None:
        if self.candidate_sale_start_provider is None:
            return None
        sale_start = self.candidate_sale_start_provider(candidate)
        return float(sale_start) if sale_start is not None else None

    def _delay_after_attempt(
        self,
        candidate: tuple[str, int],
        result: OrderResult,
        phase: str,
    ) -> float:
        if self._is_front_crowd_result(result):
            failures = self.front_crowd_failures.get(candidate, 0)
            self.front_crowd_failures[candidate] = failures + 1
            index = min(failures, len(FRONT_CROWD_BACKOFF_SECONDS) - 1)
            return FRONT_CROWD_BACKOFF_SECONDS[index]
        self.front_crowd_failures.pop(candidate, None)
        if phase == "READY":
            return PREHEAT_DELAY_SECONDS
        return SPRINT_DELAY_SECONDS

    @staticmethod
    def _is_front_crowd_result(result: OrderResult) -> bool:
        return (
            result.code in FRONT_CROWD_BUSINESS_CODES
            or result.code in RETRYABLE_HTTP_STATUS_CODES
            or "前方拥堵" in result.message
        )

    @staticmethod
    def _shorter_delay(current: float | None, candidate: float | None) -> float | None:
        if candidate is None:
            return current
        if current is None:
            return candidate
        return min(current, candidate)


@dataclass(slots=True)
class _CandidateReadiness:
    should_attempt: bool
    phase: str
    seconds_until_sale: float | None
    next_delay_seconds: float | None
