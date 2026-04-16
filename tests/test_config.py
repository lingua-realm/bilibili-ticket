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
