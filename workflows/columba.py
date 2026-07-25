"""Offline-first Columba (Android app) cache + opportunistic GitHub sync.

The medic hands the Columba APK to phones in the field — no Play Store, no
internet — so it always serves from a LOCAL cache under ``assets/apps/``. When the
medic has WiFi, :func:`sync_columba` refreshes that cache from the latest
``torlando-tech/columba`` GitHub release. Columba is **MPL-2.0**, so carrying and
serving the APK offline is permitted. Offline is a clean no-op: whatever is
already carried still installs.

Columba talks to RNodes over LoRa, so a medic-flashed RNode + Columba on a phone
is a complete pocket mesh node, onboarded entirely offline — the natural companion
to the RNode/host BIRTH path.

All network + filesystem access goes through the injected ``Connection``, so this
is fully unit-testable without a live network. The Wi-Fi-serve + QR delivery to the
phone is a separate layer that reads :func:`cached_columba`.
"""

from __future__ import annotations

import json
from typing import List, Optional

from transport.connection import Connection
from workflows.updater import SyncResult, has_connectivity

#: The app's GitHub source. Columba is MPL-2.0 (redistribution of the APK is OK).
COLUMBA_REPO = "torlando-tech/columba"
COLUMBA_LATEST_API = f"https://api.github.com/repos/{COLUMBA_REPO}/releases/latest"
COLUMBA_LICENSE = "MPL-2.0"
#: Where the carried APK lives (served to phones later). Under assets/ so it's
#: gitignored like the firmware cache — a binary blob, not source.
APPS_CACHE_DIR = "~/reticulum-tool/assets/apps"


def _shq(s: str) -> str:
    """POSIX single-quote a string so it's safe to embed in a shell command."""
    return "'" + s.replace("'", "'\\''") + "'"


def _pick_apk(assets: list) -> Optional[dict]:
    """Choose the APK to carry from a release's assets: a 'universal' build if one
    exists (installs on any CPU), otherwise the largest ``.apk`` (arch-split
    releases list several; the universal/fat one is biggest)."""
    apks = [a for a in assets
            if isinstance(a, dict) and str(a.get("name", "")).lower().endswith(".apk")]
    if not apks:
        return None
    for a in apks:
        if "universal" in str(a.get("name", "")).lower():
            return a
    return max(apks, key=lambda a: a.get("size") or 0)


def _fetch_latest_release(connection: Connection) -> dict:
    """The latest release as ``{tag, name, url, size}`` for its APK, or {} on any
    failure (offline, rate-limited, no APK asset, unparseable)."""
    code, out, _ = connection.run(
        "curl -fsSL -m 20 -H 'Accept: application/vnd.github+json' "
        + COLUMBA_LATEST_API)
    if code != 0:
        return {}
    try:
        data = json.loads(out)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    apk = _pick_apk(data.get("assets") or [])
    if not apk:
        return {}
    return {
        "tag": data.get("tag_name") or data.get("name") or "",
        "name": apk.get("name"),
        "url": apk.get("browser_download_url"),
        "size": apk.get("size"),
    }


def _cached_size(connection: Connection, path: str) -> Optional[int]:
    """Size of the already-cached file (None if it isn't there yet)."""
    code, out, _ = connection.run(f"stat -c %s {path}")
    s = out.strip()
    return int(s) if code == 0 and s.isdigit() else None


def _written_size(connection: Connection, path: str) -> Optional[int]:
    """Size of a file just downloaded — a DIFFERENT command from the cache check
    so integrity is verified independently (and each is emulator-distinguishable)."""
    code, out, _ = connection.run(f"wc -c < {path}")
    s = out.strip()
    return int(s) if code == 0 and s.isdigit() else None


def sync_columba(connection: Connection, cache_dir: str = APPS_CACHE_DIR,
                 force: bool = False) -> SyncResult:
    """Refresh the local Columba APK cache from GitHub when online.

    Offline -> clean skip (the carried APK still installs). Online -> fetch the
    latest release, and download its APK only when the cached copy is missing or a
    different size than the release asset. A download whose size doesn't match is
    discarded, never kept. Writes a ``<apk>.meta`` sidecar (version + license +
    source) and a ``.columba_version`` marker so the serve/QR layer knows what it
    has without a network.
    """
    if not has_connectivity(connection):
        return SyncResult(
            online=False,
            message=("Offline — using the Columba app already carried; install "
                     "works without internet."))

    rel = _fetch_latest_release(connection)
    if not rel:
        return SyncResult(
            online=True,
            message="Online, but could not read the latest Columba release.")

    connection.run(f"mkdir -p {cache_dir}")
    name, url, size = rel["name"], rel["url"], rel.get("size")
    path = f"{cache_dir}/{name}"
    res = SyncResult(online=True, version=rel["tag"])

    have = _cached_size(connection, path)
    if not force and have is not None and (size is None or have == size):
        res.up_to_date.append(name)
    else:
        if connection.run(f"curl -fsSL -m 300 -o {path} {url}")[0] != 0:
            res.failed.append(name)
            res.message = f"Columba {rel['tag']}: download failed."
            return res
        got = _written_size(connection, path)
        if size is not None and got != size:      # size mismatch -> corrupt
            connection.run(f"rm -f {path}")
            res.failed.append(name)
            res.message = (f"Columba {rel['tag']}: download corrupt "
                           "(size mismatch), discarded.")
            return res
        res.changed.append(name)

    meta = {"app": "Columba", "version": rel["tag"], "file": name,
            "license": COLUMBA_LICENSE, "source": url}
    connection.run(f"printf '%s' {_shq(json.dumps(meta))} > {path}.meta")
    connection.run(f"printf '%s' {_shq(rel['tag'])} > {cache_dir}/.columba_version")
    res.message = (f"Columba {rel['tag']}: "
                   + ("updated." if res.changed else "already current."))
    return res


def cached_columba(connection: Connection,
                   cache_dir: str = APPS_CACHE_DIR) -> Optional[dict]:
    """The APK the medic can serve offline right now, or None if nothing carried.
    ``{path, file, version, license}`` — the serve/QR layer's input."""
    code, out, _ = connection.run(f"ls -1 {cache_dir}/*.apk 2>/dev/null")
    if code != 0 or not out.strip():
        return None
    path = out.strip().splitlines()[-1]
    vcode, vout, _ = connection.run(f"cat {cache_dir}/.columba_version 2>/dev/null")
    return {
        "path": path,
        "file": path.rsplit("/", 1)[-1],
        "version": vout.strip() if vcode == 0 and vout.strip() else None,
        "license": COLUMBA_LICENSE,
    }
