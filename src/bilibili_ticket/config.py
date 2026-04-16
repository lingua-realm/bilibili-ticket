from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import yaml

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
            )
        )
    return AppConfig(account=account, notifier=notifier, shows=shows)
