"""Cache the medic's Python dependencies as wheels for offline cloning.

A Clone Tool run has to install rns/lxmf/segno/kivy (+ their deps) on a fresh Pi
that may have no internet. The medic is the same platform as any clone target
(Pi 5, aarch64, the same CPython), so we populate the wheelhouse by running
``pip download`` ON the medic — the wheels land natively for the right platform.
The clone then installs with ``--no-index --find-links`` from the carried
assets/packages, fully offline.

Run this while the medic has WiFi (like the firmware-cache sync). Verified on
nodemedic: 17 wheels (30 MB) install the full stack with --no-index into a clean
venv, cp313/aarch64.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from transport.connection import Connection

#: Manifest + wheelhouse, on the medic (inside the tool tree so a clone carries
#: them via push_tree).
REQUIREMENTS = "~/reticulum-tool/assets/requirements.txt"
WHEELHOUSE = "~/reticulum-tool/assets/packages"


def download_command(requirements: str = REQUIREMENTS,
                     dest: str = WHEELHOUSE) -> str:
    """pip line that fetches every wheel (and sdist fallback) the manifest needs
    into the wheelhouse, for THIS machine's platform."""
    return f"pip3 download -r {requirements} -d {dest}"


def verify_command(requirements: str = REQUIREMENTS,
                   dest: str = WHEELHOUSE) -> str:
    """Prove the wheelhouse is self-contained: a --no-index install into a
    throwaway venv must resolve everything with no network."""
    return ("rm -rf /tmp/rnm_wh_verify && python3 -m venv /tmp/rnm_wh_verify && "
            f"/tmp/rnm_wh_verify/bin/pip install --no-index --find-links {dest} "
            f"-r {requirements} && rm -rf /tmp/rnm_wh_verify")


def wheel_count(connection: Connection, dest: str = WHEELHOUSE) -> int:
    out = connection.run(f"ls {dest}/*.whl 2>/dev/null | wc -l")[1].strip()
    try:
        return int(out)
    except ValueError:
        return 0


def cache_wheels(connection: Connection, requirements: str = REQUIREMENTS,
                 dest: str = WHEELHOUSE, verify: bool = True,
                 timeout: int = 900) -> Tuple[bool, str]:
    """Populate the wheelhouse from the manifest, then (optionally) prove it
    installs offline. Returns ``(ok, message)``. Requires internet."""
    connection.run(f"mkdir -p {dest}")
    code, out, err = connection.run(download_command(requirements, dest),
                                    timeout=timeout)
    if code != 0:
        return False, (f"pip download failed (need internet): "
                       f"{(err or out)[-200:]}")
    count = wheel_count(connection, dest)
    if count == 0:
        return False, "pip download reported success but no wheels landed."
    if not verify:
        return True, f"Cached {count} wheels into the wheelhouse."
    vcode, vout, verr = connection.run(verify_command(requirements, dest),
                                       timeout=timeout)
    if vcode != 0:
        return False, (f"Cached {count} wheels but the offline install check "
                       f"failed: {(verr or vout)[-200:]}")
    return True, (f"Cached {count} wheels and verified a full offline install "
                  f"(--no-index) succeeds.")
