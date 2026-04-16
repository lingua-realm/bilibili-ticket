def test_save_and_load_session(tmp_path):
    from bilibili_ticket.bilibili.login import SessionStore

    store = SessionStore(tmp_path / "session.json")
    store.save({"SESSDATA": "abc", "bili_jct": "csrf"})

    assert store.load()["SESSDATA"] == "abc"


def test_load_missing_session_returns_empty_dict(tmp_path):
    from bilibili_ticket.bilibili.login import SessionStore

    store = SessionStore(tmp_path / "missing.json")

    assert store.load() == {}


def test_save_and_load_empty_session(tmp_path):
    from bilibili_ticket.bilibili.login import SessionStore

    store = SessionStore(tmp_path / "session.json")
    store.save({})

    assert store.load() == {}


def test_generate_qr_url_parses_response(respx_mock):
    from bilibili_ticket.bilibili.client import BilibiliClient

    respx_mock.get(
        "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
    ).respond(
        json={
            "code": 0,
            "data": {"url": "https://example.com/qr", "qrcode_key": "qr-key"},
        }
    )

    client = BilibiliClient()

    url, key = client.generate_qr_url()

    assert url == "https://example.com/qr"
    assert key == "qr-key"


def test_check_login_reads_user_profile(respx_mock):
    from bilibili_ticket.bilibili.client import BilibiliClient

    respx_mock.get("https://api.bilibili.com/x/web-interface/nav").respond(
        json={"code": 0, "data": {"mid": 1, "uname": "tester"}}
    )

    client = BilibiliClient()

    profile = client.check_login()

    assert profile["uname"] == "tester"


def test_client_uses_browser_like_user_agent_by_default():
    from bilibili_ticket.bilibili.client import BilibiliClient

    client = BilibiliClient()

    assert "Mozilla" in client.session.headers["User-Agent"]


def test_qr_code_file_renderer_writes_png(tmp_path):
    from bilibili_ticket.bilibili.login import QRCodeFileRenderer

    image_path = tmp_path / "login-qr.png"
    messages = []
    renderer = QRCodeFileRenderer(image_path=image_path, printer=messages.append)

    renderer("https://example.com/qr")

    assert image_path.exists() is True
    assert image_path.read_bytes().startswith(b"\x89PNG")
    assert str(image_path) in messages[0]
    assert "https://example.com/qr" in messages[1]


def test_qr_code_login_service_polls_until_success(tmp_path):
    from bilibili_ticket.bilibili.login import QRCodeLoginService, SessionStore

    class FakeQRClient:
        def __init__(self):
            self.poll_count = 0

        def generate_qr_url(self):
            return "https://example.com/qr", "qr-key"

        def check_qr_status(self, key):
            self.poll_count += 1
            if self.poll_count < 2:
                return False, True
            return True, False

        def check_login(self):
            return {"mid": 1, "uname": "tester"}

        def export_cookies(self):
            return {"SESSDATA": "abc", "bili_jct": "csrf"}

    store = SessionStore(tmp_path / "session.json")
    rendered_urls = []
    service = QRCodeLoginService(
        client=FakeQRClient(),
        session_store=store,
        qr_renderer=rendered_urls.append,
        sleep=lambda _: None,
    )

    profile = service.login()

    assert rendered_urls == ["https://example.com/qr"]
    assert profile["uname"] == "tester"
    assert store.load()["SESSDATA"] == "abc"


def test_qr_code_login_service_refreshes_expired_qr_until_success(tmp_path):
    from bilibili_ticket.bilibili.login import QRCodeLoginService, SessionStore

    class FakeQRClient:
        def __init__(self):
            self.keys = iter(
                [
                    ("https://example.com/qr-1", "qr-key-1"),
                    ("https://example.com/qr-2", "qr-key-2"),
                ]
            )
            self.poll_results = {
                "qr-key-1": [(False, False)],
                "qr-key-2": [(False, True), (True, False)],
            }

        def generate_qr_url(self):
            return next(self.keys)

        def check_qr_status(self, key):
            return self.poll_results[key].pop(0)

        def check_login(self):
            return {"mid": 1, "uname": "tester"}

        def export_cookies(self):
            return {"SESSDATA": "abc", "bili_jct": "csrf"}

    store = SessionStore(tmp_path / "session.json")
    rendered_urls = []
    service = QRCodeLoginService(
        client=FakeQRClient(),
        session_store=store,
        qr_renderer=rendered_urls.append,
        sleep=lambda _: None,
    )

    profile = service.login()

    assert rendered_urls == [
        "https://example.com/qr-1",
        "https://example.com/qr-2",
    ]
    assert profile["uname"] == "tester"
    assert store.load()["SESSDATA"] == "abc"
