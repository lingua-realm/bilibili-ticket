from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

import httpx
import requests


DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/135.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://show.bilibili.com/",
}


@dataclass(slots=True)
class RequestConfig:
    proxy_pool: list[str] = field(default_factory=list)
    max_concurrent_requests: int = 1
    proxy_failure_threshold: int = 2
    proxy_cooldown_seconds: float = 60.0
    proxy_backoff_seconds: float = 1.0
    risk_retry_limit: int = 3
    rate_limit_retry_limit: int = 3
    rate_limit_delay_seconds: float = 1.0
    connection_retry_limit: int = 1


@dataclass(slots=True)
class BrowserFingerprintState:
    user_agent: str = DEFAULT_HEADERS["User-Agent"]
    device_id: str = field(default_factory=lambda: uuid.uuid4().hex)


class BiliRequestError(RuntimeError):
    def __init__(self, message: str, *, response: httpx.Response | None = None):
        self.response = response
        super().__init__(message)


class BiliRateLimitError(BiliRequestError):
    code = 429


class BiliRiskControlError(BiliRequestError):
    code = 412


class BiliConnectionError(BiliRequestError):
    code = 0


@dataclass(slots=True)
class _ProxyEntry:
    url: str
    failures: int = 0
    cooldown_until: float = 0.0
    last_reason: str = ""


class ProxyPool:
    def __init__(
        self,
        proxies: list[str] | tuple[str, ...] | None = None,
        *,
        failure_threshold: int = 2,
        cooldown_seconds: float = 60.0,
        now: Callable[[], float] | None = None,
    ):
        self.entries = [
            _ProxyEntry(proxy) for proxy in self._normalize_proxies(proxies or [])
        ]
        self.failure_threshold = max(1, int(failure_threshold))
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.now = now or time.time
        self.index = 0

    @staticmethod
    def _normalize_proxies(proxies: list[str] | tuple[str, ...]) -> list[str]:
        normalized = []
        for proxy in proxies:
            text = str(proxy).strip()
            if not text or text.lower() == "none":
                continue
            normalized.append(text)
        return normalized

    @property
    def current_proxy(self) -> str | None:
        if not self.entries:
            return None
        return self.entries[self.index].url

    def current_proxy_status(self) -> str:
        return self.current_proxy or "direct"

    def status(self) -> str:
        if not self.entries:
            return "direct"
        now = self.now()
        parts = []
        for offset, entry in enumerate(self.entries):
            label = entry.url
            if entry.cooldown_until > now:
                label += f"(cooldown {entry.cooldown_until - now:.1f}s)"
            if offset == self.index:
                label = "*" + label
            parts.append(label)
        return ", ".join(parts)

    def has_available_proxy(self) -> bool:
        return any(self._is_available(entry) for entry in self.entries)

    def mark_current_success(self) -> None:
        if not self.entries:
            return
        entry = self.entries[self.index]
        entry.failures = 0
        entry.last_reason = ""

    def mark_current_failure_and_rotate(self, reason: str) -> bool:
        if not self.entries:
            return False
        entry = self.entries[self.index]
        entry.failures += 1
        entry.last_reason = reason
        if entry.failures >= self.failure_threshold:
            entry.failures = 0
            entry.cooldown_until = self.now() + self.cooldown_seconds
        return self.rotate()

    def rotate(self) -> bool:
        if not self.entries:
            return False
        for offset in range(1, len(self.entries) + 1):
            candidate_index = (self.index + offset) % len(self.entries)
            if self._is_available(self.entries[candidate_index]):
                self.index = candidate_index
                return True
        return False

    def _is_available(self, entry: _ProxyEntry) -> bool:
        return entry.cooldown_until <= self.now()


class CookieManager:
    def __init__(self, cookies: dict[str, str] | None = None):
        self.cookies = dict(cookies or {})

    def apply_to_requests_session(self, session: requests.Session) -> None:
        session.cookies.clear()
        for name, value in self.cookies.items():
            session.cookies.set(name, value)

    def apply_to_httpx_client(self, client: httpx.Client) -> None:
        client.cookies.clear()
        client.cookies.update(self.cookies)

    def update_from_response(self, response: httpx.Response) -> None:
        for cookie in response.cookies.jar:
            self.cookies[cookie.name] = cookie.value

    def export(self) -> dict[str, str]:
        return dict(self.cookies)


class BiliSession:
    def __init__(
        self,
        *,
        cookies: dict[str, str] | None = None,
        config: RequestConfig | None = None,
        browser_state: BrowserFingerprintState | None = None,
    ):
        self.config = config or RequestConfig()
        self.browser_state = browser_state or BrowserFingerprintState()
        self.cookie_manager = CookieManager(cookies)
        self.proxy_pool = ProxyPool(
            self.config.proxy_pool,
            failure_threshold=self.config.proxy_failure_threshold,
            cooldown_seconds=self.config.proxy_cooldown_seconds,
        )
        self.requests_session = requests.Session()
        self.requests_session.headers.update(self._headers())
        self.cookie_manager.apply_to_requests_session(self.requests_session)
        self._apply_current_proxy_to_requests_session()
        self._h2_client: httpx.Client | None = None

    def request_json(self, method: str, url: str, **kwargs) -> dict:
        response = self.request(method, url, **kwargs)
        payload = response.json()
        if payload.get("msg", "") == "请先登录":
            raise RuntimeError("当前未登录，请重新登陆")
        return payload

    def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        client = self._ensure_h2_client()
        try:
            response = client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.LocalProtocolError, httpx.NetworkError) as exc:
            self._invalidate_h2_client()
            raise BiliConnectionError(str(exc), response=None) from exc
        self.cookie_manager.update_from_response(response)
        self.cookie_manager.apply_to_requests_session(self.requests_session)
        if response.status_code == 412:
            raise BiliRiskControlError("HTTP 412 风控", response=response)
        if response.status_code == 429:
            raise BiliRateLimitError("HTTP 429 Too Many Requests", response=response)
        response.raise_for_status()
        self.proxy_pool.mark_current_success()
        return response

    def prewarm_connection(self, url: str) -> None:
        client = self._ensure_h2_client()
        try:
            client.head(url)
        except httpx.HTTPError:
            self._invalidate_h2_client()

    def mark_failure_and_switch_proxy(self, reason: str) -> bool:
        switched = self.proxy_pool.mark_current_failure_and_rotate(reason)
        if switched:
            self._invalidate_h2_client()
            self._apply_current_proxy_to_requests_session()
        return switched

    def current_proxy_status(self) -> str:
        return self.proxy_pool.current_proxy_status()

    def proxy_pool_status(self) -> str:
        return self.proxy_pool.status()

    def export_cookies(self) -> dict[str, str]:
        return self.cookie_manager.export()

    def _ensure_h2_client(self) -> httpx.Client:
        if self._h2_client is None:
            self._h2_client = self._build_h2_client()
        self.cookie_manager.apply_to_httpx_client(self._h2_client)
        return self._h2_client

    def _build_h2_client(self) -> httpx.Client:
        kwargs = {
            "http2": True,
            "headers": self._headers(),
            "timeout": 10.0,
        }
        proxy = self.proxy_pool.current_proxy
        if proxy is not None:
            kwargs["proxy"] = proxy
        return httpx.Client(**kwargs)

    def _headers(self) -> dict[str, str]:
        headers = dict(DEFAULT_HEADERS)
        headers["User-Agent"] = self.browser_state.user_agent
        return headers

    def _apply_current_proxy_to_requests_session(self) -> None:
        self.requests_session.proxies.clear()
        proxy = self.proxy_pool.current_proxy
        if proxy is not None:
            self.requests_session.proxies.update({"http": proxy, "https": proxy})

    def _invalidate_h2_client(self) -> None:
        if self._h2_client is None:
            return
        self._h2_client.close()
        self._h2_client = None
