def test_import_package():
    import bilibili_ticket

    assert bilibili_ticket.__all__ == []


def test_cli_help(capsys):
    from bilibili_ticket.app import main

    try:
        main(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    else:
        raise AssertionError("expected argparse help to exit")

    captured = capsys.readouterr()
    assert "login" in captured.out
    assert "run" in captured.out
    assert "daemon" in captured.out


def test_cli_dry_run_uses_example_config(tmp_path, capsys):
    from bilibili_ticket.app import main

    config_file = tmp_path / "tasks.yaml"
    config_file.write_text(
        """
account:
  session_file: data/session.json
notifier:
  type: wecom_webhook
  webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test
shows:
  - show_id: bw-2026
    project_id: 123456
    date_priority: [2026-05-01, 2026-05-02]
    price_priority: [680, 480]
    allowed_skus: [680, 480]
""",
        encoding="utf-8",
    )

    exit_code = main(["run", "--config", str(config_file), "--dry-run"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "bw-2026" in captured.out
    assert "2026-05-01" in captured.out


def test_cli_login_runs_qr_login_service(tmp_path, monkeypatch, capsys):
    import bilibili_ticket.app as app

    captured = {}

    class FakeClient:
        pass

    class FakeSessionStore:
        def __init__(self, path):
            captured["session_path"] = path

    class FakeLoginService:
        def __init__(self, client, session_store, qr_renderer):
            captured["client"] = client
            captured["session_store"] = session_store
            captured["qr_renderer"] = qr_renderer

        def login(self):
            return {"uname": "tester"}

    monkeypatch.setattr(app, "BilibiliClient", FakeClient)
    monkeypatch.setattr(app, "SessionStore", FakeSessionStore)
    monkeypatch.setattr(app, "QRCodeLoginService", FakeLoginService)

    exit_code = app.main(["login", "--session-file", str(tmp_path / "session.json")])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert isinstance(captured["client"], FakeClient)
    assert captured["session_path"] == tmp_path / "session.json"
    assert callable(captured["qr_renderer"]) is True
    assert "tester" in output


def test_cli_login_with_config_pushes_current_qr_to_wecom(tmp_path, monkeypatch, capsys):
    import bilibili_ticket.app as app
    from bilibili_ticket.models import AppConfig, AccountConfig, NotifierConfig, ShowTaskConfig

    config = AppConfig(
        account=AccountConfig(session_file="data/session.json"),
        notifier=NotifierConfig(
            type="wecom_webhook",
            webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
        ),
        shows=[
            ShowTaskConfig(
                show_id="bw-2026",
                project_id=123456,
                date_priority=["2026-05-01"],
                price_priority=[680],
                allowed_skus=[680],
                buyer_names=["张三"],
            )
        ],
    )
    captured = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_json(self, url, **kwargs):
            return {"data": {"name": "真实演出标题"}}

    class FakeSessionStore:
        def __init__(self, path):
            captured["session_path"] = path

    class FakeFileRenderer:
        def __init__(self, image_path):
            self.image_path = image_path
            captured["image_path"] = image_path

        def __call__(self, url):
            captured["rendered_url"] = url

    class FakeNotifier:
        def __init__(self, webhook):
            captured["webhook"] = webhook
            self.login_events = []
            captured["notifier"] = self

        def send_login_qr(self, event):
            self.login_events.append(event)

    class FakeLoginService:
        def __init__(self, client, session_store, qr_renderer):
            captured["client"] = client
            captured["session_store"] = session_store
            captured["qr_renderer"] = qr_renderer

        def login(self):
            captured["qr_renderer"]("https://example.com/login")
            return {"uname": "tester"}

    monkeypatch.setattr(app, "load_app_config", lambda path: config)
    monkeypatch.setattr(app, "BilibiliClient", FakeClient)
    monkeypatch.setattr(app, "SessionStore", FakeSessionStore)
    monkeypatch.setattr(app, "QRCodeFileRenderer", FakeFileRenderer)
    monkeypatch.setattr(app, "WeComNotifier", FakeNotifier)
    monkeypatch.setattr(app, "QRCodeLoginService", FakeLoginService)

    exit_code = app.main(
        [
            "login",
            "--session-file",
            str(tmp_path / "session.json"),
            "--config",
            str(tmp_path / "tasks.yaml"),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert captured["webhook"] == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"
    assert str(captured["image_path"]).endswith("login-qr.png")
    assert captured["rendered_url"] == "https://example.com/login"
    assert len(captured["notifier"].login_events) == 1
    event = captured["notifier"].login_events[0]
    assert event.show_id == "真实演出标题"
    assert event.login_url == "https://example.com/login"
    assert str(event.qr_image_path).endswith("login-qr.png")
    assert "tester" in output


def test_cli_run_once_calls_runtime_scheduler(tmp_path, monkeypatch):
    import bilibili_ticket.app as app
    from bilibili_ticket.models import AppConfig, AccountConfig, NotifierConfig, ShowTaskConfig

    config = AppConfig(
        account=AccountConfig(session_file="data/session.json"),
        notifier=NotifierConfig(
            type="wecom_webhook",
            webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
        ),
        shows=[
            ShowTaskConfig(
                show_id="bw-2026",
                project_id=123456,
                date_priority=["2026-05-01"],
                price_priority=[680],
                allowed_skus=[680],
                buyer_names=["张三"],
            )
        ],
    )
    captured = {}

    class FakeSessionStore:
        def __init__(self, path):
            captured["session_file"] = path

        def load(self):
            return {"SESSDATA": "abc"}

    class FakeScheduleManager:
        def __init__(self, configs, session_snapshot, runner_factory):
            captured["configs"] = configs
            captured["session_snapshot"] = session_snapshot
            self.configs = configs
            self.session_snapshot = session_snapshot
            self.runner_factory = runner_factory
            self.runners = {}

    class FakeNotifier:
        def __init__(self, webhook):
            captured["webhook"] = webhook

    def fake_run_scheduler(manager, notifier, *, once, interval=1.0, sleep=None, status_writer=None):
        captured["manager"] = manager
        captured["notifier"] = notifier
        captured["once"] = once
        captured["interval"] = interval
        captured["status_writer"] = status_writer
        return 0

    monkeypatch.setattr(app, "load_app_config", lambda path: config)
    monkeypatch.setattr(app, "SessionStore", FakeSessionStore)
    monkeypatch.setattr(app, "ScheduleManager", FakeScheduleManager)
    monkeypatch.setattr(app, "WeComNotifier", FakeNotifier)
    monkeypatch.setattr(app, "run_scheduler", fake_run_scheduler)

    exit_code = app.main(["run", "--config", str(tmp_path / "tasks.yaml"), "--once"])

    assert exit_code == 0
    assert captured["session_file"] == "data/session.json"
    assert captured["session_snapshot"] == {"SESSDATA": "abc"}
    assert captured["webhook"] == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"
    assert captured["once"] is True
    assert callable(captured["status_writer"]) is True


def test_cli_run_waits_for_login_and_then_calls_runtime_scheduler(tmp_path, monkeypatch):
    import bilibili_ticket.app as app
    from bilibili_ticket.models import AppConfig, AccountConfig, NotifierConfig, ShowTaskConfig

    config = AppConfig(
        account=AccountConfig(session_file="data/session.json"),
        notifier=NotifierConfig(
            type="wecom_webhook",
            webhook="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test",
        ),
        shows=[
            ShowTaskConfig(
                show_id="bw-2026",
                project_id=123456,
                date_priority=["2026-05-01"],
                price_priority=[680],
                allowed_skus=[680],
                buyer_names=["张三"],
            )
        ],
    )
    captured = {}
    session_state = {}

    class FakeSessionStore:
        def __init__(self, path):
            captured["session_file"] = path

        def load(self):
            return dict(session_state)

    class FakeNotifier:
        def __init__(self, webhook):
            captured["webhook"] = webhook
            self.login_events = []
            captured["notifier"] = self

        def send_login_qr(self, event):
            self.login_events.append(event)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def get_json(self, url, **kwargs):
            return {"data": {"name": "真实演出标题"}}

        def generate_qr_url(self):
            return "https://example.com/login", "qr-key"

    class FakeRenderer:
        def __init__(self, image_path):
            self.image_path = image_path
            captured["image_path"] = image_path

        def __call__(self, url):
            captured["rendered_url"] = url

    class FakeScheduleManager:
        def __init__(self, configs, session_snapshot, runner_factory):
            captured["configs"] = configs
            captured["session_snapshot"] = session_snapshot
            self.configs = configs
            self.session_snapshot = session_snapshot
            self.runner_factory = runner_factory
            self.runners = {}

    class FakeLoginService:
        def __init__(self, client, session_store, qr_renderer):
            captured["login_client"] = client
            captured["login_session_store"] = session_store
            captured["login_qr_renderer"] = qr_renderer

        def login(self):
            captured["login_qr_renderer"]("https://example.com/login")
            session_state.update({"SESSDATA": "abc", "bili_jct": "csrf"})
            return {"uname": "tester"}

    def fake_run_scheduler(manager, notifier, *, once, interval=1.0, sleep=None, status_writer=None):
        captured["manager"] = manager
        captured["run_notifier"] = notifier
        captured["once"] = once
        captured["status_writer"] = status_writer
        return 0

    monkeypatch.setattr(app, "load_app_config", lambda path: config)
    monkeypatch.setattr(app, "SessionStore", FakeSessionStore)
    monkeypatch.setattr(app, "WeComNotifier", FakeNotifier)
    monkeypatch.setattr(app, "BilibiliClient", FakeClient)
    monkeypatch.setattr(app, "QRCodeFileRenderer", FakeRenderer)
    monkeypatch.setattr(app, "ScheduleManager", FakeScheduleManager)
    monkeypatch.setattr(app, "QRCodeLoginService", FakeLoginService)
    monkeypatch.setattr(app, "run_scheduler", fake_run_scheduler)

    exit_code = app.main(["run", "--config", str(tmp_path / "tasks.yaml"), "--once"])

    assert exit_code == 0
    assert captured["webhook"] == "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test"
    assert str(captured["image_path"]).endswith("data/login-qr.png")
    assert captured["rendered_url"] == "https://example.com/login"
    assert len(captured["notifier"].login_events) == 1
    event = captured["notifier"].login_events[0]
    assert event.show_id == "真实演出标题"
    assert event.login_url == "https://example.com/login"
    assert str(event.qr_image_path).endswith("data/login-qr.png")
    assert captured["session_snapshot"] == {"SESSDATA": "abc", "bili_jct": "csrf"}
    assert captured["once"] is True
    assert callable(captured["status_writer"]) is True


def test_cli_daemon_delegates_to_guarded_runner(tmp_path, monkeypatch):
    import bilibili_ticket.app as app

    captured = {}

    def fake_handle_daemon(config_path, restart_delay, lock_file):
        captured["config_path"] = config_path
        captured["restart_delay"] = restart_delay
        captured["lock_file"] = lock_file
        return 0

    monkeypatch.setattr(app, "_handle_daemon", fake_handle_daemon)

    exit_code = app.main(
        [
            "daemon",
            "--config",
            str(tmp_path / "tasks.yaml"),
            "--restart-delay",
            "2.5",
            "--lock-file",
            str(tmp_path / "daemon.lock"),
        ]
    )

    assert exit_code == 0
    assert captured["config_path"] == str(tmp_path / "tasks.yaml")
    assert captured["restart_delay"] == 2.5
    assert captured["lock_file"] == str(tmp_path / "daemon.lock")
