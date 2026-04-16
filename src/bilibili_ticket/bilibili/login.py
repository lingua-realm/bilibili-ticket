from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from bilibili_ticket.bilibili.client import BilibiliClient


class SessionStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save(self, cookies: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))


class QRCodeFileRenderer:
    def __init__(
        self,
        image_path: str | Path,
        printer: Callable[[str], None] | None = None,
    ):
        self.image_path = Path(image_path)
        self.printer = printer or print

    def __call__(self, url: str) -> None:
        import qrcode

        self.image_path.parent.mkdir(parents=True, exist_ok=True)
        image = qrcode.make(url)
        image.save(self.image_path)
        self.printer(f"qr code updated: {self.image_path}")
        self.printer(url)


class QRCodeLoginService:
    def __init__(
        self,
        client: BilibiliClient,
        session_store: SessionStore,
        qr_renderer: Callable[[str], None] | None = None,
        sleep: Callable[[float], None] | None = None,
    ):
        self.client = client
        self.session_store = session_store
        self.qr_renderer = qr_renderer or self._default_qr_renderer
        if sleep is None:
            import time

            self.sleep = time.sleep
        else:
            self.sleep = sleep

    def login(self) -> dict:
        key = self._refresh_qr_code()
        while True:
            is_login, retry = self.client.check_qr_status(key)
            if is_login:
                profile = self.client.check_login()
                self.session_store.save(self.client.export_cookies())
                return profile
            if not retry:
                key = self._refresh_qr_code()
                continue
            self.sleep(1)

    def _refresh_qr_code(self) -> str:
        url, key = self.client.generate_qr_url()
        self.qr_renderer(url)
        return key

    @staticmethod
    def _default_qr_renderer(url: str) -> None:
        print(url)
