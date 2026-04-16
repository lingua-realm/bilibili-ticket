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
            if once:
                return 0
            pause(interval)
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
