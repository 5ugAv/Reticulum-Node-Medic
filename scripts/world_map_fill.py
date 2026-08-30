#!/usr/bin/env python3
"""Fill (and finish) the world-overview map tier, resumably.

The world download kept dying young — every shutdown or UI restart orphaned
it, and the medic sat for days at 10% coverage without anyone noticing
(operator found it re-downloading "from scratch", 2026-08-27/30). This
script is the fix's engine: run it any time; already-carried tiles skip
instantly, so a complete world exits in seconds and an interrupted one
resumes exactly where it died. The systemd unit beside it
(world-map-fill.service) runs it at every boot until the job is done.

Exit codes: 0 = world tier complete (or completed this run);
1 = incomplete (network down / provider trouble) — systemd retries later.
"""

import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/reticulum-tool"))

from ui.map_download import download_world, estimate_world   # noqa: E402
from ui.map_tiles import MAPS_DIR                            # noqa: E402


def main() -> int:
    os.makedirs(MAPS_DIR, exist_ok=True)
    dest = os.path.join(MAPS_DIR, "offline.mbtiles")
    total, _mb = estimate_world()
    t0 = time.time()

    def progress(s):
        if s["done"] % 1000 == 0:
            print("%s %d/%d fetched=%d skipped=%d failed=%d"
                  % (time.strftime("%H:%M:%S"), s["done"], s["total"],
                     s["fetched"], s["skipped"], s["failed"]), flush=True)

    summary = download_world(dest, on_progress=progress)
    mins = round((time.time() - t0) / 60, 1)
    print("RESULT", summary, "in", mins, "min", flush=True)
    done = summary.get("fetched", 0) + summary.get("skipped", 0)
    if summary.get("cancelled") or summary.get("blocked"):
        return 1
    return 0 if done >= total else 1


if __name__ == "__main__":
    sys.exit(main())
