from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable

from bilibili_ticket.errors import HumanInterventionRequired
from bilibili_ticket.models import OrderResult
from bilibili_ticket.scheduler.priority import expand_candidates, prioritize_available_candidates

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
        candidate_provider: Callable[[], list[tuple[str, int]]] | None = None,
        attempt_strategy: str = "stock_first",
        sprint_bypass_before_seconds: float = 5.0,
        sprint_bypass_after_seconds: float = 120.0,
        order_concurrency: int = 1,
        order_interval_ms: int = 0,
        stock_interval_ms: int | None = None,
        sleep: Callable[[float], None] | None = None,
        now: Callable[[], float] | None = None,
    ):
        self.show_id = show_id
        self.date_priority = date_priority
        self.price_priority = price_priority
        self.available_candidates_provider = available_candidates_provider
        self.order_executor = order_executor
        self.locked_order_resume_checker = locked_order_resume_checker
        self.candidate_sale_start_provider = candidate_sale_start_provider
        self.candidate_provider = candidate_provider
        self.attempt_strategy = attempt_strategy
        self.sprint_bypass_before_seconds = float(sprint_bypass_before_seconds)
        self.sprint_bypass_after_seconds = float(sprint_bypass_after_seconds)
        self.order_concurrency = max(1, int(order_concurrency))
        self.order_interval_seconds = max(0.0, float(order_interval_ms) / 1000.0)
        self.stock_interval_seconds = (
            None
            if stock_interval_ms is None
            else max(0.0, float(stock_interval_ms) / 1000.0)
        )
        self.sleep = sleep or time.sleep
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

        prioritized = self._prioritized_candidates_for_current_strategy()
        attempt_records: list[AttemptRecord] = []
        if not prioritized:
            self.last_result = ShowRunResult(
                locked_candidate=None,
                stopped_remaining_candidates=False,
                available_candidates=prioritized,
                attempt_records=attempt_records,
                next_delay_seconds=self.stock_interval_seconds,
            )
            return self.last_result
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
            candidate_result = self._attempt_candidate(candidate, readiness.phase)
            attempt_records.extend(candidate_result.records)
            if candidate_result.human_intervention is not None:
                intervention = candidate_result.human_intervention
                self.state = ShowState.PAUSED_FOR_HUMAN
                self.last_result = ShowRunResult(
                    locked_candidate=None,
                    stopped_remaining_candidates=False,
                    pause_candidate=candidate,
                    pause_reason=str(intervention),
                    available_candidates=prioritized,
                    attempt_records=attempt_records,
                    phase=phase,
                    seconds_until_sale=seconds_until_sale,
                )
                return self.last_result
            if candidate_result.successful_order is not None:
                self.state = ShowState.LOCKED
                self.last_result = ShowRunResult(
                    locked_candidate=candidate,
                    stopped_remaining_candidates=True,
                    order_result=candidate_result.successful_order,
                    available_candidates=prioritized,
                    attempt_records=attempt_records,
                )
                return self.last_result
            next_delay_seconds = self._shorter_delay(
                next_delay_seconds,
                candidate_result.next_delay_seconds,
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

    def _attempt_candidate(
        self,
        candidate: tuple[str, int],
        phase: str,
    ) -> "_CandidateAttemptResult":
        order_concurrency = self._order_concurrency_for_phase(phase)
        if order_concurrency == 1:
            outcome = self._execute_order_attempt(
                candidate,
                sequence=0,
                delay_seconds=0.0,
            )
            return self._candidate_result_from_outcomes(candidate, phase, [outcome])

        with ThreadPoolExecutor(max_workers=order_concurrency) as executor:
            futures = [
                executor.submit(
                    self._execute_order_attempt,
                    candidate,
                    sequence,
                    sequence * self.order_interval_seconds,
                )
                for sequence in range(order_concurrency)
            ]
            outcomes = [future.result() for future in as_completed(futures)]
        outcomes.sort(key=lambda outcome: outcome.sequence)
        return self._candidate_result_from_outcomes(candidate, phase, outcomes)

    def _order_concurrency_for_phase(self, phase: str) -> int:
        if phase not in {"SPRINT", "OPEN"}:
            return 1
        return self.order_concurrency

    def _execute_order_attempt(
        self,
        candidate: tuple[str, int],
        sequence: int,
        delay_seconds: float,
    ) -> "_OrderAttemptOutcome":
        if delay_seconds > 0:
            self.sleep(delay_seconds)
        try:
            result = self.order_executor(candidate)
        except HumanInterventionRequired as exc:
            return _OrderAttemptOutcome(
                sequence=sequence,
                record=AttemptRecord(
                    candidate=candidate,
                    success=False,
                    code=exc.code,
                    message=exc.message,
                ),
                human_intervention=exc,
            )
        return _OrderAttemptOutcome(
            sequence=sequence,
            record=AttemptRecord(
                candidate=candidate,
                success=result.success,
                code=result.code,
                message=result.message,
            ),
            order_result=result,
        )

    def _candidate_result_from_outcomes(
        self,
        candidate: tuple[str, int],
        phase: str,
        outcomes: list["_OrderAttemptOutcome"],
    ) -> "_CandidateAttemptResult":
        records = [outcome.record for outcome in outcomes]
        successful_order = next(
            (
                outcome.order_result
                for outcome in outcomes
                if outcome.order_result is not None and outcome.order_result.success
            ),
            None,
        )
        if successful_order is not None:
            return _CandidateAttemptResult(
                records=records,
                successful_order=successful_order,
            )

        for outcome in outcomes:
            if outcome.human_intervention is not None:
                return _CandidateAttemptResult(
                    records=records,
                    human_intervention=outcome.human_intervention,
                )

        next_delay_seconds: float | None = None
        for outcome in outcomes:
            if outcome.order_result is None:
                continue
            next_delay_seconds = self._shorter_delay(
                next_delay_seconds,
                self._delay_after_attempt(candidate, outcome.order_result, phase),
            )
        return _CandidateAttemptResult(
            records=records,
            next_delay_seconds=next_delay_seconds,
        )

    def _prioritized_candidates_for_current_strategy(self) -> list[tuple[str, int]]:
        if self.attempt_strategy in {"sprint_bypass", "auto"}:
            bypass_candidates = self._sprint_bypass_candidates()
            if bypass_candidates:
                return bypass_candidates

        stock_available_candidates = self.available_candidates_provider()
        stock_prioritized = prioritize_available_candidates(
            available_candidates=stock_available_candidates,
            date_priority=self.date_priority,
            price_priority=self.price_priority,
        )
        return self._apply_attempt_strategy(
            stock_prioritized,
            stock_available_candidates,
        )

    def _apply_attempt_strategy(
        self,
        stock_prioritized: list[tuple[str, int]],
        stock_available_candidates: list[tuple[str, int]],
    ) -> list[tuple[str, int]]:
        if self.attempt_strategy == "stock_first":
            return stock_prioritized
        if self.attempt_strategy not in {"sprint_bypass", "auto"}:
            raise ValueError(f"unsupported attempt_strategy: {self.attempt_strategy}")

        ordered = list(stock_prioritized)
        seen = set(ordered)
        stock_available_set = set(stock_available_candidates)
        for candidate in self._sprint_bypass_candidates():
            if candidate not in seen and candidate not in stock_available_set:
                ordered.append(candidate)
                seen.add(candidate)
        return ordered

    def _sprint_bypass_candidates(self) -> list[tuple[str, int]]:
        candidates = []
        for candidate in self._candidate_universe():
            if not self._should_bypass_stock(candidate):
                continue
            readiness = self._candidate_readiness(candidate)
            if readiness.phase in {"SPRINT", "OPEN"} and readiness.should_attempt:
                candidates.append(candidate)
        return candidates

    def _should_bypass_stock(self, candidate: tuple[str, int]) -> bool:
        if self.attempt_strategy == "sprint_bypass":
            return True
        if self.attempt_strategy != "auto":
            return False
        sale_start = self._candidate_sale_start(candidate)
        if sale_start is None:
            return False
        seconds_until_sale = sale_start - self.now()
        return (
            -self.sprint_bypass_after_seconds
            <= seconds_until_sale
            <= self.sprint_bypass_before_seconds
        )

    def _candidate_universe(self) -> list[tuple[str, int]]:
        if self.candidate_provider is not None:
            return self.candidate_provider()
        return expand_candidates(self.date_priority, self.price_priority)

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
        sprint_window_seconds = self._sprint_window_seconds()
        if seconds_until_sale > sprint_window_seconds:
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

    def _sprint_window_seconds(self) -> float:
        if self.attempt_strategy == "auto":
            return max(SPRINT_WINDOW_SECONDS, self.sprint_bypass_before_seconds)
        return SPRINT_WINDOW_SECONDS

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


@dataclass(slots=True)
class _OrderAttemptOutcome:
    sequence: int
    record: AttemptRecord
    order_result: OrderResult | None = None
    human_intervention: HumanInterventionRequired | None = None


@dataclass(slots=True)
class _CandidateAttemptResult:
    records: list[AttemptRecord]
    successful_order: OrderResult | None = None
    human_intervention: HumanInterventionRequired | None = None
    next_delay_seconds: float | None = None
