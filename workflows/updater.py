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
import re
from dataclasses import dataclass, field
from shlex import quote as _shq
from typing import List, Optional

from transport.connection import Connection

# A firmware filename is a KEY in release.json, which is fetched over plain HTTPS
# from a public release and is NOT authenticated (see the manifest-signing TODO
# in _fetch_manifest). Every key must therefore be treated as hostile: it becomes
# both a filesystem path and — via the download URL — a shell argument that
# Connection.run() feeds to a shell. Allow a bare filename only, and never "..".
_SAFE_FW_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


def _safe_fw_name(name) -> bool:
    """True only for a plain firmware filename safe to put in a path/command."""
    return (isinstance(name, str) and bool(name)
            and ".." not in name and _SAFE_FW_NAME.match(name) is not None)

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
    #: Downloaded and size-checked, but its bytes could NOT be hash-verified
    #: because the release published no sha256 to check them against. A distinct
    #: state on purpose: not silently trusted as "changed", not thrown away as
    #: "failed" — the operator is told it is carried-but-unverified.
    unverified: List[str] = field(default_factory=list)
    version: Optional[str] = None
    message: str = ""


def has_connectivity(connection: Connection, url: str = CONNECTIVITY_URL) -> bool:
    """True if the node can reach the internet (fast HEAD probe)."""
    return connection.run(f"curl -fsI -m 5 {url}")[0] == 0


def _fetch_manifest(connection: Connection) -> dict:
    """The release.json map ``{filename: {hash, version}}``, or {} on failure.

    TODO(security, needs key decision): this manifest is fetched over plain HTTPS
    with NO cryptographic authentication of its own. The per-file sha256s below
    are only as trustworthy as the manifest that carries them — anyone who can
    serve this URL (a MITM, a compromised release) chooses both the bytes AND the
    hash they are checked against. The real fix is a pinned publisher public key
    verifying a signature over release.json; that is deliberately NOT implemented
    here because it requires a key-management decision (which key, rotation, where
    it lives on the medic). Until then, filenames are charset-validated and every
    interpolated value is shell-quoted so a hostile manifest cannot inject a
    command or escape the cache directory — but it is not yet authenticated.
    """
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

    # A malformed manifest (entries that aren't dicts) must fail HONESTLY,
    # not raise AttributeError out of a background sync (2026-08-01 bug hunt).
    try:
        first = next(iter(manifest.values()))
        version = first.get("version") if isinstance(first, dict) else None
    except Exception:                      # noqa: BLE001
        version = None
    if not version:
        return SyncResult(
            online=True,
            message="Online, but the firmware manifest is malformed — keeping "
                    "the carried cache.")
    # The version is also a remote manifest value that becomes a path component
    # and a shell argument. Coerce to str (a JSON number would break shlex.quote)
    # and quote it everywhere it is interpolated below. The RNODE_UPDATE_DIR
    # prefix stays UNQUOTED on purpose so its leading ``~`` still expands.
    version = str(version)
    dest = f"{RNODE_UPDATE_DIR}/{_shq(version)}"
    connection.run(f"mkdir -p {dest}")

    res = SyncResult(online=True, version=version)
    for fname, info in sorted(manifest.items()):
        if not _safe_fw_name(fname):
            # A crafted key ("../../etc/x", "a;reboot", spaces) never reaches a
            # path or a shell — skip it, report it, keep syncing the honest rest.
            res.failed.append(f"{str(fname)[:64]} (unsafe firmware name — skipped)")
            continue
        want = info.get("hash") if isinstance(info, dict) else None
        if not isinstance(want, str) or not want:
            # No manifest hash means nothing to verify a download against. An
            # unverifiable firmware file is not trusted or flashed — report it.
            res.failed.append(f"{fname} (no manifest hash — skipped)")
            continue
        path = f"{dest}/{_shq(fname)}"
        if not force and _sha256(connection, path) == want:
            res.up_to_date.append(fname)
        else:
            url = f"{FIRMWARE_DL_BASE}{version}/{fname}"
            if connection.run(f"curl -fsSL -m 120 -o {path} {_shq(url)}")[0] != 0:
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
        # verified"). Write/backfill it for every good file. ``path`` already
        # ends in the quoted filename, so the ``.version`` suffix rides outside
        # that quote and the shell concatenates it onto the same word.
        connection.run(f"printf '%s %s' {_shq(version)} {_shq(want)} > {path}.version")

    connection.run(
        f"printf '%s' {_shq(version)} > {RNODE_UPDATE_DIR}/.rnm_bundle_version")

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
