from __future__ import annotations

import argparse
from pathlib import Path

from bilibili_ticket.bilibili.client import BilibiliClient
from bilibili_ticket.bilibili.login import (
    QRCodeFileRenderer,
    QRCodeLoginService,
    SessionStore,
)
from bilibili_ticket.bilibili.order_service import OrderService
from bilibili_ticket.config import load_app_config
from bilibili_ticket.notifier.wecom import (
    LoginQRCodeEvent,
    WeComNotifier,
)
from bilibili_ticket.runtime import run_scheduler
from bilibili_ticket.scheduler.manager import ScheduleManager
from bilibili_ticket.scheduler.priority import expand_candidates
from bilibili_ticket.scheduler.show_runner import ShowRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bilibili-ticket")
    subparsers = parser.add_subparsers(dest="command")

    login_parser = subparsers.add_parser("login")
    login_parser.add_argument("--session-file", required=True)
    login_parser.add_argument("--config")

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--once", action="store_true")

    return parser


def _handle_login(session_file: str, config_path: str | None = None) -> int:
    config = load_app_config(config_path) if config_path is not None else None
    profile = _run_login_flow(session_file=session_file, config=config)
    username = profile.get("uname") or str(profile.get("mid", "unknown"))
    print(f"login success: {username}")
    return 0


def _handle_run(config_path: str, dry_run: bool, once: bool) -> int:
    config = load_app_config(config_path)
    notifier = _build_notifier(config.notifier.type, config.notifier.webhook)
    session_store = SessionStore(config.account.session_file)
    if dry_run:
        print(f"loaded {len(config.shows)} show task(s)")
        for show in config.shows:
            print(f"show: {show.show_id}")
            for candidate in expand_candidates(show.date_priority, show.price_priority):
                print(f"candidate: {candidate[0]} / {candidate[1]}")
        return 0

    session = session_store.load()
    if not session:
        profile = _run_login_flow(
            session_file=config.account.session_file,
            config=config,
        )
        username = profile.get("uname") or str(profile.get("mid", "unknown"))
        print(f"login success: {username}")
        session = session_store.load()
        if not session:
            print("session file is still empty after login")
            return 1

    manager = ScheduleManager(
        configs=config.shows,
        session_snapshot=session,
        runner_factory=_build_runner_factory(),
    )
    return run_scheduler(manager=manager, notifier=notifier, once=once)


def _build_runner_factory():
    def factory(show_config, session_snapshot):
        client = BilibiliClient(cookies=session_snapshot)
        order_service = OrderService(client)
        runtime = order_service.build_show_runtime(show_config)
        runner = ShowRunner(
            show_id=show_config.show_id,
            date_priority=show_config.date_priority,
            price_priority=show_config.price_priority,
            available_candidates_provider=lambda: order_service.list_available_candidates(runtime),
            order_executor=lambda candidate: order_service.attempt_candidate(runtime, candidate),
        )
        runner.display_name = runtime.project_name
        return runner

    return factory


def _build_notifier(notifier_type: str, webhook: str):
    if notifier_type != "wecom_webhook":
        raise ValueError(f"unsupported notifier type: {notifier_type}")
    return WeComNotifier(webhook)


def _run_login_flow(session_file: str, config=None) -> dict:
    session_path = Path(session_file)
    session_store = SessionStore(session_path)
    client = BilibiliClient()
    qr_renderer = QRCodeFileRenderer(session_path.parent / "login-qr.png")
    if config is not None:
        notifier = _build_notifier(config.notifier.type, config.notifier.webhook)
        show_name = _format_project_titles(
            [show.project_id for show in config.shows],
            client=client,
        )
        qr_renderer = _build_login_qr_renderer(
            qr_renderer=qr_renderer,
            notifier=notifier,
            show_name=show_name,
        )
    service = QRCodeLoginService(
        client=client,
        session_store=session_store,
        qr_renderer=qr_renderer,
    )
    return service.login()


def _build_login_qr_renderer(qr_renderer, notifier, show_name: str):
    def render(url: str) -> None:
        qr_renderer(url)
        image_path = getattr(qr_renderer, "image_path", None)
        if image_path is None:
            return
        notifier.send_login_qr(
            LoginQRCodeEvent(
                show_id=show_name,
                login_url=url,
                qr_image_path=str(image_path),
            )
        )

    return render


def _format_project_titles(
    project_ids: list[int],
    client: BilibiliClient | None = None,
) -> str:
    unique_ids = list(dict.fromkeys(project_ids))
    if not unique_ids:
        return "未配置演出"
    titles: list[str] = []
    active_client = client or BilibiliClient()
    for project_id in unique_ids:
        title = _fetch_project_title(active_client, project_id)
        titles.append(title or f"项目ID {project_id}")
    return "、".join(titles)


def _fetch_project_title(client: BilibiliClient, project_id: int) -> str | None:
    try:
        payload = client.get_json(
            "https://show.bilibili.com/api/ticket/project/getV2",
            params={"version": 134, "id": project_id, "project_id": project_id},
        )
    except Exception:
        return None
    data = payload.get("data", {})
    name = data.get("name")
    if not name:
        return None
    return str(name)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "login":
        return _handle_login(args.session_file, args.config)
    if args.command == "run":
        return _handle_run(args.config, args.dry_run, args.once)

    parser.print_help()
    raise SystemExit(0)


if __name__ == "__main__":
    raise SystemExit(main())
