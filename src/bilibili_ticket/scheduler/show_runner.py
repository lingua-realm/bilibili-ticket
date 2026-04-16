from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

from bilibili_ticket.errors import HumanInterventionRequired
from bilibili_ticket.models import OrderResult
from bilibili_ticket.scheduler.priority import prioritize_available_candidates


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
    ):
        self.show_id = show_id
        self.date_priority = date_priority
        self.price_priority = price_priority
        self.available_candidates_provider = available_candidates_provider
        self.order_executor = order_executor
        self.locked_order_resume_checker = locked_order_resume_checker
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
        for candidate in prioritized:
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
        self.last_result = ShowRunResult(
            locked_candidate=None,
            stopped_remaining_candidates=False,
            available_candidates=prioritized,
            attempt_records=attempt_records,
        )
        return self.last_result

    def _should_resume_after_lock(self) -> bool:
        if self.locked_order_resume_checker is None:
            return False
        order_result = self.last_result.order_result
        if order_result is None or order_result.order_id is None:
            return False
        return self.locked_order_resume_checker(order_result)
