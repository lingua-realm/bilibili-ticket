from __future__ import annotations

from datetime import date
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml

from bilibili_ticket.bilibili.request import RequestConfig
from bilibili_ticket.models import AppConfig, AccountConfig, NotifierConfig, ShowTaskConfig


class ConfigError(ValueError):
    pass


def _stringify_dates(values: list[Any]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        if isinstance(value, date):
            normalized.append(value.isoformat())
        else:
            normalized.append(str(value))
    return normalized


def load_app_config(path: str | Path) -> AppConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))

    account = AccountConfig(session_file=raw["account"]["session_file"])
    request = _load_request_config(raw.get("request", {}))
    notifier = NotifierConfig(
        type=raw["notifier"]["type"],
        webhook=raw["notifier"]["webhook"],
    )
    shows = []
    for item in raw["shows"]:
        date_priority = _stringify_dates(item["date_priority"])
        price_priority = list(item["price_priority"])
        if not date_priority or not price_priority:
            raise ConfigError("priority lists must not be empty")
        shows.append(
            ShowTaskConfig(
                show_id=item["show_id"],
                project_id=item["project_id"],
                date_priority=date_priority,
                price_priority=price_priority,
                allowed_skus=list(item["allowed_skus"]),
                count=int(item.get("count", 1)),
                buyer_names=list(item.get("buyer_names", [])),
                contact_name=item.get("contact_name"),
                contact_phone=item.get("contact_phone"),
                attempt_strategy=_normalize_attempt_strategy(
                    item.get("attempt_strategy", "stock_first")
                ),
                sale_start_at=_parse_sale_start_at(item.get("sale_start_at")),
                sprint_bypass_before_seconds=float(
                    item.get("sprint_bypass_before_seconds", 5.0)
                ),
                sprint_bypass_after_seconds=float(
                    item.get("sprint_bypass_after_seconds", 120.0)
                ),
                order_concurrency=_positive_int(
                    item.get("order_concurrency", 1),
                    "order_concurrency",
                ),
                order_interval_ms=_non_negative_int(
                    item.get("order_interval_ms", 0),
                    "order_interval_ms",
                ),
                stock_interval_ms=_optional_non_negative_int(
                    item.get("stock_interval_ms"),
                    "stock_interval_ms",
                ),
            )
        )
    return AppConfig(account=account, notifier=notifier, shows=shows, request=request)


def _load_request_config(raw: dict[str, Any]) -> RequestConfig:
    return RequestConfig(
        proxy_pool=[str(proxy) for proxy in raw.get("proxy_pool", [])],
        max_concurrent_requests=_positive_int(
            raw.get("max_concurrent_requests", 1),
            "max_concurrent_requests",
        ),
        proxy_failure_threshold=int(raw.get("proxy_failure_threshold", 2)),
        proxy_cooldown_seconds=float(raw.get("proxy_cooldown_seconds", 60.0)),
        proxy_backoff_seconds=float(raw.get("proxy_backoff_seconds", 1.0)),
        risk_retry_limit=int(raw.get("risk_retry_limit", 3)),
        rate_limit_retry_limit=int(raw.get("rate_limit_retry_limit", 3)),
        rate_limit_delay_seconds=float(raw.get("rate_limit_delay_seconds", 1.0)),
        connection_retry_limit=int(raw.get("connection_retry_limit", 1)),
    )


def _normalize_attempt_strategy(value: Any) -> str:
    strategy = str(value or "stock_first").strip()
    if strategy not in {"stock_first", "sprint_bypass", "auto"}:
        raise ConfigError(f"unsupported attempt_strategy: {strategy}")
    return strategy


def _parse_sale_start_at(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return float(text)

    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ConfigError(f"unsupported sale_start_at: {text}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return parsed.timestamp()


def _positive_int(value: Any, field_name: str) -> int:
    normalized = int(value)
    if normalized < 1:
        raise ConfigError(f"{field_name} must be >= 1")
    return normalized


def _non_negative_int(value: Any, field_name: str) -> int:
    normalized = int(value)
    if normalized < 0:
        raise ConfigError(f"{field_name} must be >= 0")
    return normalized


def _optional_non_negative_int(value: Any, field_name: str) -> int | None:
    if value in (None, ""):
        return None
    return _non_negative_int(value, field_name)
