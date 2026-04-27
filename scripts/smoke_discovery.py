"""Two Discovery instances on the same host should see each other within a few seconds."""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picophone.net.discovery import Discovery


def main() -> None:
    seen_by_alice: dict[str, tuple[str, int]] = {}
    seen_by_bob:   dict[str, tuple[str, int]] = {}
    found = threading.Event()

    def on_added_alice(i: str, h: str, p: int) -> None:
        seen_by_alice[i] = (h, p)
        if "bob" in seen_by_alice and "alice" in seen_by_bob:
            found.set()

    def on_added_bob(i: str, h: str, p: int) -> None:
        seen_by_bob[i] = (h, p)
        if "bob" in seen_by_alice and "alice" in seen_by_bob:
            found.set()

    a = Discovery("alice", 11676, on_added_alice, lambda _i: None)
    b = Discovery("bob",   11686, on_added_bob,   lambda _i: None)

    try:
        ok = found.wait(timeout=8.0)
        if ok:
            print(f"OK: alice sees {seen_by_alice}")
            print(f"OK: bob   sees {seen_by_bob}")
        else:
            print(f"PARTIAL: alice={seen_by_alice}  bob={seen_by_bob}")
            sys.exit(1)
    finally:
        a.close(); b.close()


if __name__ == "__main__":
    main()
