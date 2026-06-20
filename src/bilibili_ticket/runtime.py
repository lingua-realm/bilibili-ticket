from __future__ import annotations

import time
from collections.abc import Callable

from bilibili_ticket.notifier.wecom import (
    HumanInterventionEvent,
    LockSuccessEvent,
)


def run_scheduler(
    manager,
    notifier,
    *,
    once: bool = False,
    interval: float = 1.0,
    sleep: Callable[[float], None] | None = None,
    status_writer: Callable[[list[str]], None] | None = None,
) -> int:
    pause = sleep or time.sleep
    sent_lock_events: set[tuple[str, int]] = set()
    sent_human_events: set[tuple[str, tuple[str, int], str]] = set()

    try:
        while True:
            manager.run_iteration()
            _dispatch_runner_events(
                manager=manager,
                notifier=notifier,
                sent_lock_events=sent_lock_events,
                sent_human_events=sent_human_events,
            )
            if status_writer is not None:
                status_writer(_collect_iteration_status_lines(manager))
            if once:
                return 0
            pause(_next_delay_seconds(manager, interval))
    except KeyboardInterrupt:
        return 130


def _dispatch_runner_events(
    manager,
    notifier,
    *,
    sent_lock_events: set[tuple[str, int]],
    sent_human_events: set[tuple[str, tuple[str, int], str]],
) -> None:
    for show_id, runner in manager.runners.items():
        display_name = getattr(runner, "display_name", None) or show_id
        state_name = getattr(getattr(runner, "state", None), "name", "")
        last_result = getattr(runner, "last_result", None)
        if last_result is None:
            continue

        order_result = getattr(last_result, "order_result", None)
        locked_candidate = getattr(last_result, "locked_candidate", None)
        if (
            state_name == "LOCKED"
            and order_result is not None
            and order_result.order_id is not None
            and locked_candidate is not None
        ):
            event_key = (show_id, order_result.order_id)
            if event_key not in sent_lock_events:
                notifier.send_lock_success(
                    LockSuccessEvent(
                        show_id=display_name,
                        candidate=locked_candidate,
                        order_id=order_result.order_id,
                        order_url=order_result.order_url,
                        pay_money=order_result.pay_money,
                        pay_remain_seconds=order_result.pay_remain_seconds,
                        buyer_summary=order_result.buyer_summary,
                        ticket_name=order_result.ticket_name,
                    )
                )
                sent_lock_events.add(event_key)

        pause_candidate = getattr(last_result, "pause_candidate", None)
        pause_reason = getattr(last_result, "pause_reason", None)
        if (
            state_name == "PAUSED_FOR_HUMAN"
            and pause_candidate is not None
            and pause_reason
        ):
            event_key = (show_id, pause_candidate, pause_reason)
            if event_key not in sent_human_events:
                notifier.send_human_takeover(
                    HumanInterventionEvent(
                        show_id=display_name,
                        candidate=pause_candidate,
                        reason=pause_reason,
                    )
                )
                sent_human_events.add(event_key)


def _collect_iteration_status_lines(manager) -> list[str]:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    lines: list[str] = []
    for show_id, runner in manager.runners.items():
        display_name = getattr(runner, "display_name", None) or show_id
        state_name = getattr(getattr(runner, "state", None), "name", "")
        last_result = getattr(runner, "last_result", None)
        available_candidates = getattr(last_result, "available_candidates", []) if last_result else []
        attempt_records = getattr(last_result, "attempt_records", []) if last_result else []
        locked_candidate = getattr(last_result, "locked_candidate", None) if last_result else None
        order_result = getattr(last_result, "order_result", None) if last_result else None
        pause_reason = getattr(last_result, "pause_reason", None) if last_result else None
        phase = getattr(last_result, "phase", None) if last_result else None
        seconds_until_sale = getattr(last_result, "seconds_until_sale", None) if last_result else None

        parts = [
            f"[{timestamp}]",
            f"演出={display_name}",
            f"状态={state_name or 'UNKNOWN'}",
            f"可用={_format_candidates(available_candidates)}",
        ]
        if phase:
            parts.append(f"阶段={phase}")
        if seconds_until_sale is not None:
            parts.append(f"开售倒计时={_format_countdown(seconds_until_sale)}")
        if attempt_records:
            parts.append(f"尝试={_format_attempts(attempt_records)}")
        if locked_candidate is not None and order_result is not None and order_result.order_id is not None:
            parts.append(
                f"锁单={_format_candidate(locked_candidate)} 订单={order_result.order_id}"
            )
        if pause_reason:
            parts.append(f"暂停={pause_reason}")
        lines.append(" ".join(parts))
    return lines


def _format_candidates(candidates: list[tuple[str, int]]) -> str:
    if not candidates:
        return "无"
    return ",".join(_format_candidate(candidate) for candidate in candidates)


def _next_delay_seconds(manager, default_interval: float) -> float:
    delays = []
    for runner in manager.runners.values():
        last_result = getattr(runner, "last_result", None)
        delay = getattr(last_result, "next_delay_seconds", None) if last_result else None
        if delay is not None:
            delays.append(float(delay))
    if not delays:
        return default_interval
    return min(delays)


def _format_countdown(seconds: float) -> str:
    if seconds <= 0:
        return "已开售"
    return f"{seconds:.1f}s"


def _format_attempts(attempt_records) -> str:
    return "; ".join(
        (
            f"{_format_candidate(record.candidate)} -> "
            f"{'成功' if record.success else '失败'} "
            f"code={record.code} {record.message}"
        ).strip()
        for record in attempt_records
    )


def _format_candidate(candidate: tuple[str, int]) -> str:
    date_text, price = candidate
    return f"{date_text}/{price / 100:.2f}元"
