"""Two Discovery instances on the same host should see each other."""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from picophone.net.discovery import Discovery


def main() -> None:
    seen_a: dict[str, tuple[str, int]] = {}
    seen_b: dict[str, tuple[str, int]] = {}

    a = Discovery("alice", 11676,
                  on_added=lambda i, h, p: seen_a.update({i: (h, p)}),
                  on_removed=lambda i: seen_a.pop(i, None))
    b = Discovery("bob",   11686,
                  on_added=lambda i, h, p: seen_b.update({i: (h, p)}),
                  on_removed=lambda i: seen_b.pop(i, None))

    deadline = time.time() + 15.0
    while time.time() < deadline:
        if "bob" in seen_a and "alice" in seen_b:
            break
        time.sleep(0.2)

    a.close(); b.close()
    print(f"alice saw: {seen_a}")
    print(f"bob   saw: {seen_b}")
    if "bob" in seen_a and "alice" in seen_b:
        print("OK: pure-Python multicast discovery roundtrip")
    else:
        print("FAIL: peers did not find each other within 15 s")
        sys.exit(1)


if __name__ == "__main__":
    main()
