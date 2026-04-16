import pytest


def test_run_guarded_restarts_after_failure_and_cleans_up_lock_file(tmp_path):
    from bilibili_ticket.daemon import run_guarded

    attempts = {"count": 0}
    sleep_calls = []
    lock_file = tmp_path / "guard.lock"

    def target():
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("boom")
        return 130

    exit_code = run_guarded(
        target=target,
        lock_file=lock_file,
        restart_delay=2.5,
        sleep=sleep_calls.append,
    )

    assert exit_code == 130
    assert attempts["count"] == 2
    assert sleep_calls == [2.5]
    assert lock_file.exists() is False


def test_process_lock_rejects_second_live_instance(tmp_path):
    from bilibili_ticket.daemon import AlreadyRunningError, ProcessLock

    lock_file = tmp_path / "guard.lock"
    with ProcessLock(lock_file):
        with pytest.raises(AlreadyRunningError):
            with ProcessLock(lock_file):
                pass
