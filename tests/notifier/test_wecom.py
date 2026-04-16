def test_send_lock_success_message(respx_mock):
    from bilibili_ticket.notifier.wecom import LockSuccessEvent, WeComNotifier

    route = respx_mock.post("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test").respond(
        json={"errcode": 0, "errmsg": "ok"}
    )
    notifier = WeComNotifier("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test")

    notifier.send_lock_success(
        LockSuccessEvent(
            show_id="bw-2026",
            candidate=("2026-05-01", 680),
            order_id=9527,
        )
    )

    payload = route.calls.last.request.read().decode("utf-8")
    assert "锁单成功" in payload
    assert "9527" in payload


def test_send_human_takeover_message(respx_mock):
    from bilibili_ticket.notifier.wecom import HumanInterventionEvent, WeComNotifier

    route = respx_mock.post("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test").respond(
        json={"errcode": 0, "errmsg": "ok"}
    )
    notifier = WeComNotifier("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test")

    notifier.send_human_takeover(
        HumanInterventionEvent(
            show_id="bw-2026",
            candidate=("2026-05-01", 680),
            reason="[100044] captcha",
        )
    )

    payload = route.calls.last.request.read().decode("utf-8")
    assert "人工接管" in payload
    assert "100044" in payload


def test_send_human_takeover_message_with_qr_image_and_link(tmp_path, respx_mock):
    from bilibili_ticket.notifier.wecom import HumanInterventionEvent, WeComNotifier

    qr_image = tmp_path / "login-qr.png"
    qr_image.write_bytes(b"fake-image-bytes")
    route = respx_mock.post("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test").respond(
        json={"errcode": 0, "errmsg": "ok"}
    )
    notifier = WeComNotifier("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test")

    notifier.send_human_takeover(
        HumanInterventionEvent(
            show_id="scheduler",
            candidate=None,
            reason="session file is empty, please login again",
            login_url="https://example.com/login",
            qr_image_path=str(qr_image),
        )
    )

    assert len(route.calls) == 2
    markdown_payload = route.calls[0].request.read().decode("utf-8")
    image_payload = route.calls[1].request.read().decode("utf-8")
    assert "[点击重新登录](https://example.com/login)" in markdown_payload
    assert str(qr_image) in markdown_payload
    assert '"msgtype":"image"' in image_payload


def test_send_login_qr_message_with_link_and_image(tmp_path, respx_mock):
    from bilibili_ticket.notifier.wecom import LoginQRCodeEvent, WeComNotifier

    qr_image = tmp_path / "login-qr.png"
    qr_image.write_bytes(b"fake-image-bytes")
    route = respx_mock.post("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test").respond(
        json={"errcode": 0, "errmsg": "ok"}
    )
    notifier = WeComNotifier("https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test")

    notifier.send_login_qr(
        LoginQRCodeEvent(
            show_id="真实演出标题",
            login_url="https://example.com/login",
            qr_image_path=str(qr_image),
        )
    )

    assert len(route.calls) == 2
    markdown_payload = route.calls[0].request.read().decode("utf-8")
    image_payload = route.calls[1].request.read().decode("utf-8")
    assert "登录二维码已更新" in markdown_payload
    assert "[点击扫码登录](https://example.com/login)" in markdown_payload
    assert "真实演出标题" in markdown_payload
    assert '"msgtype":"image"' in image_payload
