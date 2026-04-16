from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from pathlib import Path

import httpx


@dataclass(slots=True)
class LockSuccessEvent:
    show_id: str
    candidate: tuple[str, int]
    order_id: int


@dataclass(slots=True)
class HumanInterventionEvent:
    show_id: str
    reason: str
    candidate: tuple[str, int] | None = None
    login_url: str | None = None
    qr_image_path: str | None = None


@dataclass(slots=True)
class LoginQRCodeEvent:
    show_id: str
    login_url: str
    qr_image_path: str


class WeComNotifier:
    def __init__(self, webhook: str):
        self.webhook = webhook
        self.client = httpx.Client(timeout=10.0)

    def send_lock_success(self, event: LockSuccessEvent) -> None:
        date_text, price = event.candidate
        self._send_markdown(
            (
                f"**锁单成功**\n"
                f"> 演出: `{event.show_id}`\n"
                f"> 日期: `{date_text}`\n"
                f"> 票价: `{price}`\n"
                f"> 订单号: `{event.order_id}`"
            )
        )

    def send_human_takeover(self, event: HumanInterventionEvent) -> None:
        lines = [
            "**人工接管**",
            f"> 演出: `{event.show_id}`",
        ]
        if event.candidate is not None:
            date_text, price = event.candidate
            lines.append(f"> 日期: `{date_text}`")
            lines.append(f"> 票价: `{price}`")
        lines.append(f"> 原因: `{event.reason}`")
        if event.login_url:
            lines.append(f"> 登录链接: [点击重新登录]({event.login_url})")
        if event.qr_image_path:
            lines.append(f"> 二维码文件: `{event.qr_image_path}`")
        self._send_markdown("\n".join(lines))
        if event.qr_image_path:
            self._send_image(Path(event.qr_image_path))

    def send_login_qr(self, event: LoginQRCodeEvent) -> None:
        self._send_markdown(
            (
                f"**登录二维码已更新**\n"
                f"> 演出: `{event.show_id}`\n"
                f"> 登录链接: [点击扫码登录]({event.login_url})\n"
                f"> 二维码文件: `{event.qr_image_path}`"
            )
        )
        self._send_image(Path(event.qr_image_path))

    def _send_markdown(self, content: str) -> None:
        response = self.client.post(
            self.webhook,
            json={
                "msgtype": "markdown",
                "markdown": {"content": content},
            },
        )
        response.raise_for_status()

    def _send_image(self, image_path: Path) -> None:
        if not image_path.exists():
            return
        content = image_path.read_bytes()
        response = self.client.post(
            self.webhook,
            json={
                "msgtype": "image",
                "image": {
                    "base64": base64.b64encode(content).decode("ascii"),
                    "md5": hashlib.md5(content).hexdigest(),
                },
            },
        )
        response.raise_for_status()
