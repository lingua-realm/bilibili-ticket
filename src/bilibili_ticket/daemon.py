from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path


class AlreadyRunningError(RuntimeError):
    def __init__(self, pid: int, lock_file: Path):
        self.pid = pid
        self.lock_file = lock_file
        super().__init__(f"监控守护进程已在运行，PID={pid}，锁文件={lock_file}")


class ProcessLock:
    def __init__(self, lock_file: str | Path):
        self.lock_file = Path(lock_file)
        self._acquired = False

    def __enter__(self):
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        existing_pid = self._read_pid()
        if existing_pid is not None and self._pid_exists(existing_pid):
            raise AlreadyRunningError(existing_pid, self.lock_file)
        if self.lock_file.exists():
            self.lock_file.unlink()
        fd = os.open(self.lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        try:
            os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
        finally:
            os.close(fd)
        self._acquired = True
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._acquired and self.lock_file.exists():
            self.lock_file.unlink()
        self._acquired = False

    def _read_pid(self) -> int | None:
        if not self.lock_file.exists():
            return None
        try:
            content = self.lock_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not content.isdigit():
            return None
        return int(content)

    @staticmethod
    def _pid_exists(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True


def run_guarded(
    target: Callable[[], int],
    lock_file: str | Path,
    *,
    restart_delay: float = 3.0,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    with ProcessLock(lock_file):
        while True:
            try:
                exit_code = target()
            except KeyboardInterrupt:
                return 130
            except Exception as exc:
                print(f"监控异常退出：{exc}，{restart_delay:.1f} 秒后重启")
                sleep(restart_delay)
                continue

            if exit_code == 130:
                return 130

            print(f"监控已退出，退出码 {exit_code}，{restart_delay:.1f} 秒后重启")
            sleep(restart_delay)
