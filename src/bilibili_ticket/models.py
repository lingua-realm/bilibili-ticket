from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class AccountConfig:
    session_file: str


@dataclass(slots=True)
class NotifierConfig:
    type: str
    webhook: str


@dataclass(slots=True)
class ShowTaskConfig:
    show_id: str
    project_id: int
    date_priority: list[str]
    price_priority: list[int]
    allowed_skus: list[int]
    count: int = 1
    buyer_names: list[str] = field(default_factory=list)
    contact_name: str | None = None
    contact_phone: str | None = None


@dataclass(slots=True)
class AppConfig:
    account: AccountConfig
    notifier: NotifierConfig
    shows: list[ShowTaskConfig]


@dataclass(slots=True)
class PreparedOrder:
    project_id: int
    screen_id: int
    sku_id: int
    count: int
    buyer_info: list[dict] | str
    token: str
    ptoken: str
    order_type: int = 1
    pay_money: int = 0
    id_bind: int = 1
    contact_name: str | None = None
    contact_phone: str | None = None
    device_id: str = "device"


@dataclass(slots=True)
class OrderResult:
    success: bool
    code: int
    message: str
    order_id: int | None = None


@dataclass(slots=True)
class CandidateInfo:
    date: str
    price: int
    screen_id: int
    sku_id: int
    screen_name: str
    sku_desc: str


@dataclass(slots=True)
class ShowRuntime:
    show_id: str
    project_id: int
    project_name: str
    project_buyer_info: str
    id_bind: int
    count: int
    selected_buyers: list[dict]
    contact_name: str | None
    contact_phone: str | None
    candidates: dict[tuple[str, int], CandidateInfo]
