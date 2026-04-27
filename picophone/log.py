from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_path: Path) -> None:
    fmt = logging.Formatter("%(asctime)s  %(levelname)s  %(name)s  %(message)s",
                            datefmt="%d/%m/%Y %H:%M:%S")
    file = RotatingFileHandler(log_path, maxBytes=512_000, backupCount=3, encoding="utf-8")
    file.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file)
    root.addHandler(console)


def call_event(direction: str, identity: str, addr: str) -> None:
    logging.getLogger("call").info("%s call from %s (%s)", direction, identity, addr)
