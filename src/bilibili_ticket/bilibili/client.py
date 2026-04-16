from __future__ import annotations

import uuid

import httpx


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


class BilibiliClient:
    """Minimal HTTP client wrapper for Bilibili ticket APIs."""

    def __init__(self, cookies: dict[str, str] | None = None):
        self.cookies = cookies or {}
        self.session = httpx.Client(
            cookies=self.cookies,
            headers=DEFAULT_HEADERS,
            timeout=10.0,
        )
        self.device_id = uuid.uuid4().hex

    def get_json(self, url: str, **kwargs) -> dict:
        response = self.session.get(url, **kwargs)
        response.raise_for_status()
        return response.json()

    def post_json(self, url: str, **kwargs) -> dict:
        response = self.session.post(url, **kwargs)
        response.raise_for_status()
        return response.json()

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
        return {cookie.name: cookie.value for cookie in self.session.cookies.jar}
