import httpx
import pytest


def test_proxy_pool_switches_to_next_available_proxy_after_failure():
    from bilibili_ticket.bilibili.request import ProxyPool

    now = [100.0]
    pool = ProxyPool(
        ["http://proxy-a:8080", "http://proxy-b:8080"],
        failure_threshold=1,
        cooldown_seconds=30,
        now=lambda: now[0],
    )

    assert pool.current_proxy == "http://proxy-a:8080"

    switched = pool.mark_current_failure_and_rotate("HTTP 412")

    assert switched is True
    assert pool.current_proxy == "http://proxy-b:8080"
    assert "cooldown" in pool.status()


def test_client_retries_http_412_by_switching_proxy(respx_mock):
    from bilibili_ticket.bilibili.client import BilibiliClient
    from bilibili_ticket.bilibili.request import RequestConfig

    route = respx_mock.get("https://show.bilibili.com/api/test")
    route.side_effect = [
        httpx.Response(412),
        httpx.Response(200, json={"code": 0, "data": {"ok": True}}),
    ]
    client = BilibiliClient(
        request_config=RequestConfig(
            proxy_pool=["http://proxy-a:8080", "http://proxy-b:8080"],
            proxy_failure_threshold=1,
            proxy_cooldown_seconds=30,
            risk_retry_limit=2,
        ),
        sleep=lambda _: None,
    )

    payload = client.get_json("https://show.bilibili.com/api/test")

    assert payload["data"]["ok"] is True
    assert route.call_count == 2
    assert client.current_proxy_status().startswith("http://proxy-b")


def test_client_raises_rate_limit_error_after_retry_limit(respx_mock):
    from bilibili_ticket.bilibili.client import BilibiliClient
    from bilibili_ticket.bilibili.request import BiliRateLimitError, RequestConfig

    route = respx_mock.post("https://show.bilibili.com/api/test").respond(
        status_code=429,
        json={"message": "Too Many Requests"},
    )
    sleeps = []
    client = BilibiliClient(
        request_config=RequestConfig(
            rate_limit_retry_limit=2,
            rate_limit_delay_seconds=0.5,
        ),
        sleep=sleeps.append,
    )

    with pytest.raises(BiliRateLimitError):
        client.post_json("https://show.bilibili.com/api/test", json={"x": 1})

    assert route.call_count == 3
    assert sleeps == [0.5, 0.5]


def test_client_prewarms_http2_connection_with_head_request(respx_mock):
    from bilibili_ticket.bilibili.client import BilibiliClient

    route = respx_mock.head("https://show.bilibili.com/").respond(status_code=204)
    client = BilibiliClient()

    client.prewarm_connection("https://show.bilibili.com/")

    assert route.called is True
