from __future__ import annotations

from pathlib import Path


class IterationStatusWriter:
    def __init__(self, log_file: str | Path):
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, lines: list[str]) -> None:
        if not lines:
            return
        text = "\n".join(lines)
        print(text, flush=True)
        with self.log_file.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")
