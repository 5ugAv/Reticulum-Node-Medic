#!/usr/bin/env python3
"""Fill the medic's carried .deb cache — run on the medic while it is online.

    python3 scripts/refresh_deb_cache.py

Downloads (never installs; no root) every package in
workflows.wheelhouse.ALL_PACKAGES — Dire Wolf + ALSA for salvaged radios and
the `cage` kiosk stack a cloned medic needs to open a window — into
assets/packages/debs, and prints what is there afterwards. MITOSIS reads
that cache; a medic built from GitHub has it empty until this is run once
(readiness sweep, 2026-10-03).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from transport.connection import LocalConnection  # noqa: E402
from workflows.wheelhouse import ALL_PACKAGES, DEB_CACHE, cache_debs, deb_count  # noqa: E402


def main() -> int:
    closure = "--closure" in sys.argv[1:]      # the whole dependency tree, for an offline clone
    conn = LocalConnection()
    before = deb_count(conn)
    res = cache_debs(conn, closure=closure, timeout=1800)
    after = deb_count(conn)
    print("[debs] packages:", ", ".join(ALL_PACKAGES))
    print("[debs] result:", res)
    print("[debs] %s: %d -> %d .deb files" % (DEB_CACHE, before, after))
    return 0 if after > before or after > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
