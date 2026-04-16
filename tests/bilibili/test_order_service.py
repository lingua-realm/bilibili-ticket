import json

import httpx
import pytest


@pytest.fixture
def order_service():
    from bilibili_ticket.bilibili.client import BilibiliClient
    from bilibili_ticket.bilibili.order_service import OrderService

    return OrderService(BilibiliClient())


@pytest.fixture
def show_task_config():
    from bilibili_ticket.models import ShowTaskConfig

    return ShowTaskConfig(
        show_id="bw-2026",
        project_id=1,
        date_priority=["2026-05-01", "2026-05-02"],
        price_priority=[680, 480],
        allowed_skus=[680, 480],
        count=1,
        buyer_names=["张三"],
    )


def test_check_stock_returns_true_when_stock_status_is_3(order_service, respx_mock):
    respx_mock.post("https://show.bilibili.com/api/ticket/stock/check").respond(
        json={"code": 0, "data": {"stockStatus": 3}}
    )

    assert order_service.check_stock(project_id=1, screen_id=2, sku_id=3) is True


def test_check_stock_returns_true_when_code_is_missing_but_stock_status_is_3(order_service, respx_mock):
    respx_mock.post("https://show.bilibili.com/api/ticket/stock/check").respond(
        json={"data": {"stockStatus": 3}}
    )

    assert order_service.check_stock(project_id=1, screen_id=2, sku_id=3) is True


def test_check_stock_returns_false_when_stock_status_is_not_3(order_service, respx_mock):
    respx_mock.post("https://show.bilibili.com/api/ticket/stock/check").respond(
        json={"code": 0, "data": {"stockStatus": 2}}
    )

    assert order_service.check_stock(project_id=1, screen_id=2, sku_id=3) is False


@pytest.mark.parametrize("response_code", [-401, 100044, 412])
def test_prepare_order_pauses_for_human_on_risk_code(
    order_service,
    respx_mock,
    response_code,
):
    respx_mock.post(
        "https://show.bilibili.com/api/ticket/order/prepare?project_id=1"
    ).respond(
        json={"code": response_code, "message": "risk", "data": {"ga_data": {}}},
    )

    from bilibili_ticket.errors import HumanInterventionRequired

    with pytest.raises(HumanInterventionRequired, match="触发风控校验，请人工接管"):
        order_service.prepare_order(
            project_id=1,
            screen_id=2,
            sku_id=3,
            count=1,
            buyer_info=[],
        )


def test_prepare_order_accepts_errno_success_and_sends_required_fields(order_service, respx_mock):
    route = respx_mock.post(
        "https://show.bilibili.com/api/ticket/order/prepare?project_id=1"
    ).respond(
        json={"errno": 0, "msg": "", "data": {"token": "token", "ptoken": None}},
    )

    prepared = order_service.prepare_order(
        project_id=1,
        screen_id=2,
        sku_id=3,
        count=1,
        buyer_info="buyer-token",
    )

    assert prepared.token == "token"
    assert prepared.ptoken == ""
    sent_payload = json.loads(route.calls.last.request.read().decode("utf-8"))
    assert sent_payload["order_type"] == 1
    assert sent_payload["ticket_agent"] == ""
    assert sent_payload["requestSource"] == "neul-next"
    assert sent_payload["newRisk"] is True


def test_create_order_returns_success_result(order_service, respx_mock):
    from bilibili_ticket.models import PreparedOrder

    respx_mock.post(
        "https://show.bilibili.com/api/ticket/order/createV2?project_id=1"
    ).respond(
        json={"code": 0, "message": "ok", "data": {"orderId": 9527}},
    )

    result = order_service.create_order(
        PreparedOrder(
            project_id=1,
            screen_id=2,
            sku_id=3,
            count=1,
            buyer_info=[],
            token="token",
            ptoken="",
        )
    )

    assert result.success is True
    assert result.order_id == 9527


def test_create_order_returns_unsuccessful_result_on_stock_not_enough(order_service, respx_mock):
    from bilibili_ticket.models import PreparedOrder

    respx_mock.post(
        "https://show.bilibili.com/api/ticket/order/createV2?project_id=1"
    ).respond(
        json={"code": 100009, "message": "stock not enough", "data": {}},
    )

    result = order_service.create_order(
        PreparedOrder(
            project_id=1,
            screen_id=2,
            sku_id=3,
            count=1,
            buyer_info=[],
            token="token",
            ptoken="",
        )
    )

    assert result.success is False
    assert result.code == 100009


def test_create_order_accepts_errno_response_and_sends_required_fields(order_service, respx_mock):
    from bilibili_ticket.models import PreparedOrder

    route = respx_mock.post(
        "https://show.bilibili.com/api/ticket/order/createV2?project_id=1"
    ).respond(
        json={"errno": 900001, "msg": "前方拥堵，请重试.", "data": {"orderId": None}},
    )

    result = order_service.create_order(
        PreparedOrder(
            project_id=1,
            screen_id=2,
            sku_id=3,
            count=1,
            buyer_info=[],
            token="token",
            ptoken="",
        )
    )

    assert result.success is False
    assert result.code == 900001
    assert result.message == "前方拥堵，请重试."
    sent_payload = json.loads(route.calls.last.request.read().decode("utf-8"))
    assert sent_payload["order_type"] == 1
    assert "timestamp" in sent_payload
    assert sent_payload["is_package"] == 0
    assert sent_payload["package_num"] == 1
    assert "clickPosition" in sent_payload


def test_build_show_runtime_resolves_candidates_and_buyers(
    order_service,
    respx_mock,
    show_task_config,
):
    respx_mock.get("https://show.bilibili.com/api/ticket/project/getV2").respond(
        json={
            "code": 0,
            "data": {
                "name": "Bili World",
                "project_type": 2,
                "buyer_info": "buyer-token",
                "id_bind": 1,
            },
        }
    )
    route = respx_mock.get("https://show.bilibili.com/api/ticket/project/infoByDate")
    route.side_effect = [
        httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "screen_list": [
                        {
                            "id": 11,
                            "name": "2026-05-01 场次",
                            "ticket_list": [
                                {"id": 101, "price": 680, "desc": "680档"},
                                {"id": 102, "price": 480, "desc": "480档"},
                            ],
                        }
                    ]
                },
            },
        ),
        httpx.Response(
            200,
            json={
                "code": 0,
                "data": {
                    "screen_list": [
                        {
                            "id": 12,
                            "name": "2026-05-02 场次",
                            "ticket_list": [
                                {"id": 201, "price": 680, "desc": "680档"},
                            ],
                        }
                    ]
                },
            },
        ),
    ]
    respx_mock.get("https://show.bilibili.com/api/ticket/buyer/list").respond(
        json={
            "code": 0,
            "data": {
                "list": [
                    {
                        "id": 1,
                        "name": "张三",
                        "tel": "13800000000",
                        "personal_id": "310101199001010011",
                        "id_type": 0,
                    }
                ]
            },
        }
    )

    runtime = order_service.build_show_runtime(show_task_config)

    assert runtime.project_name == "Bili World"
    assert list(runtime.candidates) == [
        ("2026-05-01", 680),
        ("2026-05-01", 480),
        ("2026-05-02", 680),
    ]
    assert runtime.selected_buyers[0]["name"] == "张三"


def test_attempt_candidate_prepares_and_creates_order(order_service, respx_mock):
    from bilibili_ticket.models import CandidateInfo, ShowRuntime

    respx_mock.post(
        "https://show.bilibili.com/api/ticket/order/prepare?project_id=1"
    ).respond(
        json={"code": 0, "message": "ok", "data": {"token": "token", "ptoken": ""}},
    )
    create_route = respx_mock.post(
        "https://show.bilibili.com/api/ticket/order/createV2?project_id=1"
    ).respond(
        json={"code": 0, "message": "ok", "data": {"orderId": 9527}},
    )
    runtime = ShowRuntime(
        show_id="bw-2026",
        project_id=1,
        project_name="Bili World",
        project_buyer_info="buyer-token",
        id_bind=1,
        count=1,
        selected_buyers=[
            {
                "id": 1,
                "name": "张三",
                "tel": "13800000000",
                "personal_id": "310101199001010011",
                "id_type": 0,
            }
        ],
        contact_name=None,
        contact_phone=None,
        candidates={
            ("2026-05-01", 680): CandidateInfo(
                date="2026-05-01",
                price=680,
                screen_id=11,
                sku_id=101,
                screen_name="2026-05-01 场次",
                sku_desc="680档",
            )
        },
    )

    result = order_service.attempt_candidate(runtime, ("2026-05-01", 680))

    assert result.success is True
    sent_payload = create_route.calls.last.request.read().decode("utf-8")
    assert "buyer_info" in sent_payload
    assert "101" in sent_payload


def test_attempt_candidate_returns_failed_result_when_prepare_is_rejected(order_service, respx_mock):
    from bilibili_ticket.models import CandidateInfo, ShowRuntime

    respx_mock.post(
        "https://show.bilibili.com/api/ticket/order/prepare?project_id=1"
    ).respond(
        json={"code": 83000005, "message": "不能为null", "data": None},
    )
    create_route = respx_mock.post(
        "https://show.bilibili.com/api/ticket/order/createV2?project_id=1"
    ).respond(
        json={"code": 0, "message": "ok", "data": {"orderId": 9527}},
    )
    runtime = ShowRuntime(
        show_id="bw-2026",
        project_id=1,
        project_name="Bili World",
        project_buyer_info="buyer-token",
        id_bind=1,
        count=1,
        selected_buyers=[
            {
                "id": 1,
                "name": "张三",
                "tel": "13800000000",
                "personal_id": "310101199001010011",
                "id_type": 0,
            }
        ],
        contact_name=None,
        contact_phone=None,
        candidates={
            ("2026-05-01", 680): CandidateInfo(
                date="2026-05-01",
                price=680,
                screen_id=11,
                sku_id=101,
                screen_name="2026-05-01 场次",
                sku_desc="680档",
            )
        },
    )

    result = order_service.attempt_candidate(runtime, ("2026-05-01", 680))

    assert result.success is False
    assert result.code == 83000005
    assert result.message == "不能为null"
    assert create_route.called is False
