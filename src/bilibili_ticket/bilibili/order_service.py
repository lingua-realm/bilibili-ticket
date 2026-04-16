from __future__ import annotations

import json
import time
from typing import Iterable

from bilibili_ticket.bilibili.client import BilibiliClient
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
            self._collect_candidates(
                candidates=candidates,
                screens=project_data.get("screen_list", []),
                date_text=show.date_priority[0],
                allowed_prices=set(show.allowed_skus),
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
                )

    def _select_buyers(self, show: ShowTaskConfig, id_bind: int) -> list[dict]:
        if id_bind == 0:
            return []
        buyer_payload = self.client.get_json(
            "https://show.bilibili.com/api/ticket/buyer/list",
            params={"nomask": 1},
        )
        buyers = buyer_payload.get("data", {}).get("list", [])
        selected = [buyer for buyer in buyers if buyer.get("name") in show.buyer_names]
        if len(selected) != show.count:
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
        response_code = self._response_code(response)
        if response_code in {-401, 100044, 412}:
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
        data = response.get("data", {})
        code = self._response_code(response)
        return OrderResult(
            success=code == 0,
            code=code if code is not None else -1,
            message=self._response_message(response),
            order_id=data.get("orderId"),
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
        return self.create_order(prepared)

    @staticmethod
    def _response_code(response: dict) -> int | None:
        if "code" in response:
            return response.get("code")
        return response.get("errno")

    @staticmethod
    def _response_message(response: dict) -> str:
        return str(response.get("message") or response.get("msg") or "")

    @staticmethod
    def _build_click_position(timestamp: int) -> dict[str, int]:
        return {
            "x": 326,
            "y": 724,
            "origin": timestamp - 21356,
            "now": timestamp,
        }
