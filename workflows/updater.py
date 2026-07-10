"""Offline-first RNode firmware cache + opportunistic GitHub sync.

Field builds have no internet, so flashing always runs from a LOCAL firmware
cache (``rnodeconf --autoinstall --nocheck``, reading
``~/.config/rnodeconf/update/<version>/``). When the medic Pi has WiFi,
``sync_firmware()`` refreshes that cache from the official RNode_Firmware GitHub
release — hash-verified against the release manifest — and
``check_tool_update()`` sees whether the tool's own checkout is behind its
remote. Offline is a clean no-op, never a failure: whatever is carried still
flashes.

All network + filesystem access goes through the injected ``Connection``, so
this is fully unit-testable without a radio or a live network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional

from transport.connection import Connection

#: Official RNode firmware release manifest + download base (markqvist).
FIRMWARE_VERSION_URL = (
    "https://github.com/markqvist/RNode_Firmware/releases/latest/download/"
    "release.json")
FIRMWARE_DL_BASE = (
    "https://github.com/markqvist/RNode_Firmware/releases/download/")
#: Where rnodeconf looks for cached firmware (so --nocheck flashes offline).
RNODE_UPDATE_DIR = "~/.config/rnodeconf/update"
#: Cheap reachability probe target.
CONNECTIVITY_URL = "https://github.com"
#: The tool's own checkout on the medic (for self-update checks).
TOOL_DIR = "~/reticulum-tool"


@dataclass
class SyncResult:
    online: bool
    changed: List[str] = field(default_factory=list)      # downloaded/updated
    up_to_date: List[str] = field(default_factory=list)    # already current
    failed: List[str] = field(default_factory=list)        # download/verify failed
    version: Optional[str] = None
    message: str = ""


def has_connectivity(connection: Connection, url: str = CONNECTIVITY_URL) -> bool:
    """True if the node can reach the internet (fast HEAD probe)."""
    return connection.run(f"curl -fsI -m 5 {url}")[0] == 0


def _fetch_manifest(connection: Connection) -> dict:
    """The release.json map ``{filename: {hash, version}}``, or {} on failure."""
    code, out, _ = connection.run(f"curl -fsSL -m 20 {FIRMWARE_VERSION_URL}")
    if code != 0:
        return {}
    try:
        data = json.loads(out)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _sha256(connection: Connection, path: str) -> Optional[str]:
    code, out, _ = connection.run(f"sha256sum {path}")
    if code != 0:
        return None
    parts = out.split()
    return parts[0] if parts else None


def sync_firmware(connection: Connection, force: bool = False) -> SyncResult:
    """Refresh the local RNode firmware cache from GitHub when online.

    Offline -> clean skip (the carried firmware still flashes). Online -> fetch
    the release manifest and, for each firmware file, download it only when the
    cached copy is missing or its sha256 doesn't match the manifest. A download
    whose hash can't be verified is discarded and reported as failed, never kept.
    """
    if not has_connectivity(connection):
        return SyncResult(
            online=False,
            message=("Offline — using the firmware already carried; flashing "
                     "works without internet."))

    manifest = _fetch_manifest(connection)
    if not manifest:
        return SyncResult(
            online=True,
            message="Online, but could not read the firmware manifest.")

    version = next(iter(manifest.values())).get("version")
    dest = f"{RNODE_UPDATE_DIR}/{version}"
    connection.run(f"mkdir -p {dest}")

    res = SyncResult(online=True, version=version)
    for fname, info in sorted(manifest.items()):
        want = info.get("hash")
        path = f"{dest}/{fname}"
        if not force and _sha256(connection, path) == want:
            res.up_to_date.append(fname)
        else:
            url = f"{FIRMWARE_DL_BASE}{version}/{fname}"
            if connection.run(f"curl -fsSL -m 120 -o {path} {url}")[0] != 0:
                res.failed.append(fname)
                continue
            if _sha256(connection, path) == want:
                res.changed.append(fname)
            else:
                res.failed.append(fname)
                connection.run(f"rm -f {path}")   # don't keep a corrupt file
                continue
        # rnodeconf --autoinstall verifies each firmware against a sidecar
        # "<file>.version" holding "<version> <hash>". Without it the offline
        # flash aborts ("No release hash found ... integrity could not be
        # verified"). Write/backfill it for every good file.
        connection.run(f"printf '%s %s' {version} {want} > {path}.version")

    connection.run(f"printf '%s' {version} > {RNODE_UPDATE_DIR}/.rnm_bundle_version")

    parts = []
    if res.changed:
        parts.append(f"{len(res.changed)} updated")
    if res.up_to_date:
        parts.append(f"{len(res.up_to_date)} current")
    if res.failed:
        parts.append(f"{len(res.failed)} failed")
    res.message = (f"Firmware {version}: " + ", ".join(parts) if parts
                   else f"Firmware {version}: nothing to do.")
    return res


def autoinstall_command(port: str, version: Optional[str] = None,
                        offline: bool = True) -> str:
    """The rnodeconf autoinstall command. Offline (default) adds ``--nocheck``
    so it flashes purely from the local cache and never hits the network."""
    parts = [f"rnodeconf {port} --autoinstall"]
    if version:
        parts.append(f"--fw-version {version}")
    if offline:
        parts.append("--nocheck")
    return " ".join(parts)


def check_tool_update(connection: Connection, tool_dir: str = TOOL_DIR,
                      branch: str = "main") -> dict:
    """When online, report whether the tool's checkout is behind its remote.

    Uses a plain ``git fetch`` + ``rev-list --count`` so it never mutates the
    working tree — applying the update is a separate, explicit action.
    """
    if not has_connectivity(connection):
        return {"online": False, "update_available": False, "behind": 0,
                "message": "Offline — tool update check skipped."}
    if connection.run(f"git -C {tool_dir} fetch --quiet origin {branch}")[0] != 0:
        return {"online": True, "update_available": False, "behind": 0,
                "message": "Could not reach the tool's git remote."}
    code, out, _ = connection.run(
        f"git -C {tool_dir} rev-list --count HEAD..origin/{branch}")
    behind = int(out.strip()) if code == 0 and out.strip().isdigit() else 0
    return {"online": True, "update_available": behind > 0, "behind": behind,
            "message": (f"{behind} update(s) available." if behind > 0
                        else "Tool is up to date.")}
