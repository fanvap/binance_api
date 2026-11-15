"""Plain-text daily application log files."""

from datetime import datetime
from pathlib import Path


class AppLogWriter:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def append(self, timestamp: datetime, level: str, message: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"app_{timestamp:%Y%m%d}.txt"
        lines = message.splitlines() or [""]
        with path.open("a", encoding="utf-8", newline="\n") as log_file:
            for line in lines:
                log_file.write(
                    f"[{timestamp:%Y-%m-%d %H:%M:%S}] [{level}] {line}\n"
                )
