def test_load_config_with_date_and_price_priority(tmp_path):
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

    from bilibili_ticket.config import load_app_config

    app = load_app_config(config_file)

    assert app.shows[0].date_priority == ["2026-05-01", "2026-05-02"]
    assert app.shows[0].price_priority == [680, 480]


def test_reject_empty_priority_lists(tmp_path):
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
    date_priority: []
    price_priority: []
    allowed_skus: [680]
""",
        encoding="utf-8",
    )

    from bilibili_ticket.config import ConfigError, load_app_config

    try:
        load_app_config(config_file)
    except ConfigError as exc:
        assert "priority" in str(exc)
    else:
        raise AssertionError("expected ConfigError")


def test_load_config_runtime_fields(tmp_path):
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
    date_priority: [2026-05-01]
    price_priority: [680]
    allowed_skus: [680]
    count: 2
    buyer_names: [张三, 李四]
    contact_name: 张三
    contact_phone: 13800000000
""",
        encoding="utf-8",
    )

    from bilibili_ticket.config import load_app_config

    app = load_app_config(config_file)

    assert app.shows[0].count == 2
    assert app.shows[0].buyer_names == ["张三", "李四"]
    assert app.shows[0].contact_name == "张三"


def test_load_config_defaults_runtime_fields(tmp_path):
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
    date_priority: [2026-05-01]
    price_priority: [680]
    allowed_skus: [680]
""",
        encoding="utf-8",
    )

    from bilibili_ticket.config import load_app_config

    app = load_app_config(config_file)

    assert app.shows[0].count == 1
    assert app.shows[0].buyer_names == []
    assert app.shows[0].contact_name is None


def test_load_config_request_and_attempt_strategy(tmp_path):
    config_file = tmp_path / "tasks.yaml"
    config_file.write_text(
        """
account:
  session_file: data/session.json
request:
  proxy_pool:
    - http://proxy-a:8080
    - http://proxy-b:8080
  proxy_failure_threshold: 1
  proxy_cooldown_seconds: 30
  proxy_backoff_seconds: 2
  risk_retry_limit: 4
  rate_limit_retry_limit: 5
  rate_limit_delay_seconds: 0.5
  max_concurrent_requests: 4
notifier:
  type: wecom_webhook
  webhook: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=test
shows:
  - show_id: bw-2026
    project_id: 123456
    date_priority: [2026-05-01]
    price_priority: [680]
    allowed_skus: [680]
    attempt_strategy: sprint_bypass
    order_concurrency: 3
    order_interval_ms: 250
    stock_interval_ms: 1500
""",
        encoding="utf-8",
    )

    from bilibili_ticket.config import load_app_config

    app = load_app_config(config_file)

    assert app.request.proxy_pool == ["http://proxy-a:8080", "http://proxy-b:8080"]
    assert app.request.proxy_failure_threshold == 1
    assert app.request.proxy_cooldown_seconds == 30
    assert app.request.proxy_backoff_seconds == 2
    assert app.request.risk_retry_limit == 4
    assert app.request.rate_limit_retry_limit == 5
    assert app.request.rate_limit_delay_seconds == 0.5
    assert app.request.max_concurrent_requests == 4
    assert app.shows[0].attempt_strategy == "sprint_bypass"
    assert app.shows[0].order_concurrency == 3
    assert app.shows[0].order_interval_ms == 250
    assert app.shows[0].stock_interval_ms == 1500


def test_load_config_auto_attempt_strategy_with_manual_sale_start(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

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
    date_priority: [2026-07-11]
    price_priority: [12800]
    allowed_skus: [12800]
    attempt_strategy: auto
    sale_start_at: "2026-07-11 20:00:00"
    sprint_bypass_before_seconds: 5
    sprint_bypass_after_seconds: 120
""",
        encoding="utf-8",
    )

    from bilibili_ticket.config import load_app_config

    app = load_app_config(config_file)

    expected = datetime(
        2026,
        7,
        11,
        20,
        0,
        0,
        tzinfo=ZoneInfo("Asia/Shanghai"),
    ).timestamp()
    show = app.shows[0]
    assert show.attempt_strategy == "auto"
    assert show.sale_start_at == expected
    assert show.sprint_bypass_before_seconds == 5
    assert show.sprint_bypass_after_seconds == 120
