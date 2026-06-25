from __future__ import annotations

import json
import time
from typing import Iterable

import httpx

from bilibili_ticket.bilibili.client import BilibiliClient
from bilibili_ticket.bilibili.request import BiliRateLimitError, BiliRiskControlError
from bilibili_ticket.errors import HumanInterventionRequired, OrderPreparationFailed
from bilibili_ticket.models import (
    CandidateInfo,
    OrderResult,
    PreparedOrder,
    ShowRuntime,
    ShowTaskConfig,
)


DEFAULT_ORDER_TYPE = 1


class OrderService:
    def __init__(self, client: BilibiliClient):
        self.client = client

    def check_stock(self, project_id: int, screen_id: int, sku_id: int) -> bool:
        response = self.client.post_json(
            "https://show.bilibili.com/api/ticket/stock/check",
            json={
                "projectId": str(project_id),
                "screenId": int(screen_id),
                "skuId": int(sku_id),
            },
        )
        response_code = response.get("code")
        stock_status = response.get("data", {}).get("stockStatus")
        return stock_status == 3 and response_code in {None, 0}

    def build_show_runtime(self, show: ShowTaskConfig) -> ShowRuntime:
        project_payload = self.client.get_json(
            "https://show.bilibili.com/api/ticket/project/getV2",
            params={"version": 134, "id": show.project_id, "project_id": show.project_id},
        )
        project_data = project_payload["data"]
        candidates = self._resolve_candidates(project_data=project_data, show=show)
        selected_buyers = self._select_buyers(show=show, id_bind=project_data.get("id_bind", 1))
        return ShowRuntime(
            show_id=show.show_id,
            project_id=show.project_id,
            project_name=project_data["name"],
            project_buyer_info=project_data.get("buyer_info", ""),
            id_bind=project_data.get("id_bind", 1),
            count=show.count,
            selected_buyers=selected_buyers,
            contact_name=show.contact_name,
            contact_phone=show.contact_phone,
            candidates=candidates,
        )

    def _resolve_candidates(
        self,
        project_data: dict,
        show: ShowTaskConfig,
    ) -> dict[tuple[str, int], CandidateInfo]:
        candidates: dict[tuple[str, int], CandidateInfo] = {}
        project_type = project_data.get("project_type", 1)
        if project_type == 2:
            for date_text in show.date_priority:
                info_payload = self.client.get_json(
                    "https://show.bilibili.com/api/ticket/project/infoByDate",
                    params={"id": show.project_id, "date": date_text},
                )
                self._collect_candidates(
                    candidates=candidates,
                    screens=info_payload.get("data", {}).get("screen_list", []),
                    date_text=date_text,
                    allowed_prices=set(show.allowed_skus),
                )
        else:
            allowed_dates = set(show.date_priority)
            allowed_prices = set(show.allowed_skus)
            for screen in project_data.get("screen_list", []):
                screen_date = self._screen_date(screen)
                if screen_date not in allowed_dates:
                    continue
                self._collect_candidates(
                    candidates=candidates,
                    screens=[screen],
                    date_text=screen_date,
                    allowed_prices=allowed_prices,
                )
        return candidates

    def _collect_candidates(
        self,
        candidates: dict[tuple[str, int], CandidateInfo],
        screens: Iterable[dict],
        date_text: str,
        allowed_prices: set[int],
    ) -> None:
        for screen in screens:
            for sku in screen.get("ticket_list", []):
                price = int(sku["price"])
                key = (date_text, price)
                if price not in allowed_prices or key in candidates:
                    continue
                candidates[key] = CandidateInfo(
                    date=date_text,
                    price=price,
                    screen_id=int(screen["id"]),
                    sku_id=int(sku["id"]),
                    screen_name=screen.get("name", ""),
                    sku_desc=sku.get("desc", ""),
                    sale_start=self._sale_start(sku),
                )

    @staticmethod
    def _screen_date(screen: dict) -> str | None:
        start_time_str = screen.get("start_time_str")
        if start_time_str:
            return str(start_time_str)
        name = str(screen.get("name") or "").strip()
        return name.split()[0] if name else None

    @staticmethod
    def _sale_start(sku: dict) -> int | None:
        sale_start = sku.get("saleStart")
        if sale_start is None:
            return None
        return int(sale_start)

    def _select_buyers(self, show: ShowTaskConfig, id_bind: int) -> list[dict]:
        if id_bind == 0:
            return []
        buyer_payload = self.client.get_json(
            "https://show.bilibili.com/api/ticket/buyer/list",
            params={"nomask": 1},
        )
        buyers = buyer_payload.get("data", {}).get("list", [])
        selected = [buyer for buyer in buyers if buyer.get("name") in show.buyer_names]
        required_buyer_count = len(show.buyer_names) if show.buyer_names else show.count
        if len(selected) != required_buyer_count:
            raise ValueError("selected buyers do not match required count")
        return [
            {
                "id": buyer["id"],
                "name": buyer["name"],
                "tel": buyer["tel"],
                "personal_id": buyer["personal_id"],
                "id_type": buyer["id_type"],
            }
            for buyer in selected
        ]

    def prepare_order(
        self,
        project_id: int,
        screen_id: int,
        sku_id: int,
        count: int,
        buyer_info: object,
        order_type: int = DEFAULT_ORDER_TYPE,
    ) -> PreparedOrder:
        risk_retries = 0
        while True:
            try:
                response = self.client.post_json(
                    f"https://show.bilibili.com/api/ticket/order/prepare?project_id={project_id}",
                    json={
                        "project_id": project_id,
                        "screen_id": screen_id,
                        "order_type": order_type,
                        "sku_id": sku_id,
                        "count": count,
                        "buyer_info": buyer_info,
                        "ticket_agent": "",
                        "token": "",
                        "requestSource": "neul-next",
                        "newRisk": True,
                    },
                )
            except BiliRiskControlError as exc:
                raise HumanInterventionRequired(
                    code=exc.code,
                    message="触发风控校验，代理重试耗尽，请人工接管",
                ) from exc
            except BiliRateLimitError as exc:
                raise OrderPreparationFailed(
                    code=exc.code,
                    message=str(exc) or "HTTP 429 Too Many Requests",
                ) from exc

            response_code = self._response_code(response)
            if response_code == 412:
                if self._recover_after_risk_control("prepare returned 412", risk_retries):
                    risk_retries += 1
                    continue
                raise HumanInterventionRequired(
                    code=response_code,
                    message="触发风控校验，代理重试耗尽，请人工接管",
                )
            break
        if response_code in {-401, 100044}:
            raise HumanInterventionRequired(
                code=response_code,
                message="触发风控校验，请人工接管",
            )
        data = response.get("data")
        if response_code not in {0, None} or not isinstance(data, dict):
            raise OrderPreparationFailed(
                code=response_code if response_code is not None else -1,
                message=self._response_message(response) or "预下单失败",
            )
        return PreparedOrder(
            project_id=project_id,
            screen_id=screen_id,
            sku_id=sku_id,
            count=count,
            buyer_info=buyer_info,
            order_type=order_type,
            token=data.get("token", ""),
            ptoken=(data.get("ptoken") or "").replace("=", ""),
        )

    def create_order(self, prepared: PreparedOrder) -> OrderResult:
        timestamp = int(time.time() * 1000)
        try:
            response = self.client.post_json(
                f"https://show.bilibili.com/api/ticket/order/createV2?project_id={prepared.project_id}",
                json={
                    "project_id": prepared.project_id,
                    "screen_id": prepared.screen_id,
                    "sku_id": prepared.sku_id,
                    "count": prepared.count,
                    "pay_money": prepared.pay_money,
                    "order_type": prepared.order_type,
                    "timestamp": timestamp,
                    "id_bind": prepared.id_bind,
                    "need_contact": 1 if prepared.id_bind == 0 else 0,
                    "is_package": 0,
                    "package_num": 1,
                    "buyer_info": json.dumps(prepared.buyer_info, ensure_ascii=False),
                    "token": prepared.token,
                    "deviceId": prepared.device_id,
                    "buyer": prepared.contact_name,
                    "tel": prepared.contact_phone,
                    "clickPosition": self._build_click_position(timestamp),
                    "requestSource": "neul-next",
                    "newRisk": True,
                },
            )
        except BiliRiskControlError as exc:
            raise HumanInterventionRequired(
                code=exc.code,
                message="触发风控校验，代理重试耗尽，请人工接管",
            ) from exc
        except BiliRateLimitError as exc:
            return OrderResult(
                success=False,
                code=exc.code,
                message=str(exc) or "HTTP 429 Too Many Requests",
            )
        except httpx.HTTPStatusError as exc:
            return self._http_status_error_result(exc)
        data = response.get("data", {})
        code = self._response_code(response)
        order_id = data.get("orderId")
        return OrderResult(
            success=code == 0,
            code=code if code is not None else -1,
            message=self._response_message(response),
            order_id=order_id,
            order_url=self._build_order_url(order_id) if order_id is not None else None,
        )

    def list_available_candidates(self, runtime: ShowRuntime) -> list[tuple[str, int]]:
        available: list[tuple[str, int]] = []
        for key, candidate in runtime.candidates.items():
            if self.check_stock(
                project_id=runtime.project_id,
                screen_id=candidate.screen_id,
                sku_id=candidate.sku_id,
            ):
                available.append(key)
        return available

    def attempt_candidate(
        self,
        runtime: ShowRuntime,
        candidate_key: tuple[str, int],
    ) -> OrderResult:
        candidate = runtime.candidates[candidate_key]
        try:
            prepared = self.prepare_order(
                project_id=runtime.project_id,
                screen_id=candidate.screen_id,
                sku_id=candidate.sku_id,
                count=runtime.count,
                buyer_info=runtime.project_buyer_info,
            )
        except httpx.HTTPStatusError as exc:
            return self._http_status_error_result(exc)
        except OrderPreparationFailed as exc:
            return OrderResult(
                success=False,
                code=exc.code,
                message=exc.message,
            )
        prepared.pay_money = candidate.price * runtime.count
        prepared.id_bind = runtime.id_bind
        prepared.contact_name = runtime.contact_name
        prepared.contact_phone = runtime.contact_phone
        prepared.buyer_info = runtime.selected_buyers
        prepared.device_id = self.client.device_id
        result = self.create_order(prepared)
        if result.success and result.order_id is not None:
            self._enrich_success_result(
                result=result,
                candidate=candidate,
                runtime=runtime,
            )
        return result

    def should_resume_locked_order(self, result: OrderResult) -> bool:
        if result.order_id is None:
            return False
        try:
            payload = self.client.get_json(
                f"https://show.bilibili.com/api/ticket/order/info?order_id={result.order_id}"
            )
        except Exception:
            return False
        data = payload.get("data", {})
        status = data.get("status")
        sub_status_name = str(data.get("sub_status_name") or "")
        pay_remain_time = data.get("pay_remain_time")
        if status == 1 and (pay_remain_time is None or pay_remain_time > 0):
            return False
        if "待支付" in sub_status_name or "待付款" in sub_status_name:
            return False
        return True

    @staticmethod
    def _response_code(response: dict) -> int | None:
        if "code" in response:
            return response.get("code")
        return response.get("errno")

    @staticmethod
    def _response_message(response: dict) -> str:
        return str(response.get("message") or response.get("msg") or "")

    def _recover_after_risk_control(self, reason: str, retry_count: int) -> bool:
        request_config = getattr(self.client, "request_config", None)
        retry_limit = int(getattr(request_config, "risk_retry_limit", 3))
        if retry_count >= retry_limit:
            return False
        recover = getattr(self.client, "recover_after_risk_control", None)
        if recover is None:
            return False
        return bool(recover(reason))

    @staticmethod
    def _http_status_error_result(exc: httpx.HTTPStatusError) -> OrderResult:
        response = exc.response
        return OrderResult(
            success=False,
            code=response.status_code,
            message=f"HTTP {response.status_code} {response.reason_phrase}".strip(),
        )

    @staticmethod
    def _build_click_position(timestamp: int) -> dict[str, int]:
        return {
            "x": 326,
            "y": 724,
            "origin": timestamp - 21356,
            "now": timestamp,
        }

    def _enrich_success_result(
        self,
        result: OrderResult,
        candidate: CandidateInfo,
        runtime: ShowRuntime,
    ) -> None:
        result.pay_money = candidate.price * runtime.count
        result.ticket_name = candidate.sku_desc or None
        result.buyer_summary = self._summarize_runtime_buyers(runtime.selected_buyers)
        try:
            payload = self.client.get_json(
                f"https://show.bilibili.com/api/ticket/order/info?order_id={result.order_id}"
            )
        except Exception:
            return
        data = payload.get("data", {})
        result.pay_money = data.get("pay_money") or result.pay_money
        result.pay_remain_seconds = data.get("pay_remain_time")
        result.ticket_name = (
            data.get("ticket_info", {}).get("name")
            or result.ticket_name
        )
        result.buyer_summary = (
            self._summarize_order_buyers(data.get("buyer_infos", []))
            or result.buyer_summary
        )

    @staticmethod
    def _summarize_runtime_buyers(buyers: list[dict]) -> str | None:
        names = [str(buyer.get("name", "")).strip() for buyer in buyers if buyer.get("name")]
        if not names:
            return None
        return "、".join(OrderService._mask_name(name) for name in names)

    @staticmethod
    def _summarize_order_buyers(buyers: list[dict]) -> str | None:
        names = [str(buyer.get("buyer", "")).strip() for buyer in buyers if buyer.get("buyer")]
        if not names:
            return None
        return "、".join(names)

    @staticmethod
    def _mask_name(name: str) -> str:
        if len(name) <= 1:
            return name
        return name[0] + "*" * (len(name) - 1)

    @staticmethod
    def _build_order_url(order_id: int) -> str:
        return f"https://show.bilibili.com/platform/orderDetail.html?order_id={order_id}"
