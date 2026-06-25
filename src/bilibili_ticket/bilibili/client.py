from __future__ import annotations

import time

from bilibili_ticket.bilibili.request import (
    BiliConnectionError,
    BiliRateLimitError,
    BiliRiskControlError,
    BiliSession,
    RequestConfig,
)


class BilibiliClient:
    """HTTP client wrapper for Bilibili ticket APIs."""

    def __init__(
        self,
        cookies: dict[str, str] | None = None,
        request_config: RequestConfig | None = None,
        sleep=None,
    ):
        self.request_config = request_config or RequestConfig()
        self.transport = BiliSession(
            cookies=cookies,
            config=self.request_config,
        )
        self.session = self.transport.requests_session
        self.device_id = self.transport.browser_state.device_id
        self.sleep = sleep or time.sleep

    def get_json(self, url: str, **kwargs) -> dict:
        return self._json_with_recovery("GET", url, **kwargs)

    def post_json(self, url: str, **kwargs) -> dict:
        return self._json_with_recovery("POST", url, **kwargs)

    def prewarm_connection(self, url: str) -> None:
        self.transport.prewarm_connection(url)

    def recover_after_risk_control(self, reason: str) -> bool:
        return self.transport.mark_failure_and_switch_proxy(reason)

    def current_proxy_status(self) -> str:
        return self.transport.current_proxy_status()

    def proxy_pool_status(self) -> str:
        return self.transport.proxy_pool_status()

    def generate_qr_url(self) -> tuple[str, str]:
        payload = self.get_json(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
        )
        data = payload["data"]
        return data["url"], data["qrcode_key"]

    def check_qr_status(self, key: str) -> tuple[bool, bool]:
        payload = self.get_json(
            "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
            params={"source": "main-fe-header", "qrcode_key": key},
        )
        code = payload["data"]["code"]
        if code == 0:
            return True, False
        if code in {86101, 86090}:
            return False, True
        return False, False

    def check_login(self) -> dict:
        payload = self.get_json("https://api.bilibili.com/x/web-interface/nav")
        return payload["data"]

    def export_cookies(self) -> dict[str, str]:
        return self.transport.export_cookies()

    def _json_with_recovery(self, method: str, url: str, **kwargs) -> dict:
        risk_retries = 0
        rate_limit_retries = 0
        connection_retries = 0
        while True:
            try:
                return self.transport.request_json(method, url, **kwargs)
            except BiliRiskControlError:
                if risk_retries >= self.request_config.risk_retry_limit:
                    raise
                if not self.recover_after_risk_control("HTTP 412"):
                    raise
                risk_retries += 1
                self.sleep(self.request_config.proxy_backoff_seconds)
            except BiliRateLimitError:
                if rate_limit_retries >= self.request_config.rate_limit_retry_limit:
                    raise
                self.transport.mark_failure_and_switch_proxy("HTTP 429")
                rate_limit_retries += 1
                self.sleep(self.request_config.rate_limit_delay_seconds)
            except BiliConnectionError:
                if connection_retries >= self.request_config.connection_retry_limit:
                    raise
                self.transport.mark_failure_and_switch_proxy("connection error")
                connection_retries += 1
                self.sleep(self.request_config.proxy_backoff_seconds)
