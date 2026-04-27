from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_path: Path, enabled: bool = True) -> None:
    fmt = logging.Formatter("%(asctime)s  %(levelname)s  %(name)s  %(message)s",
                            datefmt="%d/%m/%Y %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO if enabled else logging.WARNING)
    if enabled:
        file = RotatingFileHandler(log_path, maxBytes=512_000, backupCount=3, encoding="utf-8")
        file.setFormatter(fmt)
        root.addHandler(file)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)


def call_event(direction: str, identity: str, addr: str) -> None:
    logging.getLogger("call").info("%s call from %s (%s)", direction, identity, addr)
