"""Offline-first cache of the Reticulum PHONE apps + opportunistic GitHub sync.

The medic is the mesh's post office (a propagation node); the messaging happens on
PHONES running a Reticulum app. So the medic carries those apps' APKs offline and
hands them to phones in the field — no Play Store, no internet. A medic-flashed
RNode + one of these apps = a complete pocket mesh node, onboarded fully offline.

Two apps, user's choice (Comms screen):

* **Columba**  — torlando-tech/columba, MPL-2.0 (free redistribution).
* **Sideband** — markqvist/Sideband, CC-BY-NC-SA-4.0 *with* the author's explicit
  grant to freely distribute binaries "so long as no payment … is charged". So the
  medic may hand it out only for FREE — fine for a field tool; revisit if the medic
  is ever sold.

``sync_app`` refreshes one app's cache from its latest GitHub release when online
(size-verified, corrupt downloads discarded); offline is a clean no-op. Everything
goes through an injected ``Connection`` — unit-testable without a live network. The
Wi-Fi-serve + QR layer that pushes an APK to a phone reads ``cached_apps``.
"""

from __future__ import annotations

import json
from typing import List, Optional

from transport.connection import Connection
from workflows.updater import SyncResult, has_connectivity

#: Cache dir (served to phones later). Under assets/ so it's gitignored like firmware.
APPS_CACHE_DIR = "~/reticulum-tool/assets/apps"

#: The catalogue. ``free_redistribution`` gates whether the medic may serve it.
APPS = {
    "columba": {
        "name": "Columba",
        "repo": "torlando-tech/columba",
        "license": "MPL-2.0",
        "free_redistribution": True,
        "blurb": "A simple, modern Reticulum messenger — connects over Bluetooth, "
                 "Wi-Fi or LoRa. Clean Material Design; easy for newcomers.",
    },
    "sideband": {
        "name": "Sideband",
        "repo": "markqvist/Sideband",
        "license": "CC-BY-NC-SA-4.0 (free binary redistribution granted)",
        "free_redistribution": True,
        "blurb": "The original Reticulum LXMF client — messaging, voice calls, maps "
                 "and telemetry. More features; by Reticulum's author.",
    },
}


def _api(repo: str) -> str:
    return f"https://api.github.com/repos/{repo}/releases/latest"


def _shq(s: str) -> str:
    """POSIX single-quote a string so it's safe inside a shell command."""
    return "'" + s.replace("'", "'\\''") + "'"


def _pick_apk(assets: list) -> Optional[dict]:
    """Choose the APK to carry: a 'universal' build if present (any CPU), else the
    largest ``.apk`` (arch-split releases list several; the fat one is biggest)."""
    apks = [a for a in assets
            if isinstance(a, dict) and str(a.get("name", "")).lower().endswith(".apk")]
    if not apks:
        return None
    for a in apks:
        if "universal" in str(a.get("name", "")).lower():
            return a
    return max(apks, key=lambda a: a.get("size") or 0)


def _fetch_latest_release(connection: Connection, repo: str) -> dict:
    """Latest release as ``{tag, name, url, size}`` for its APK, or {} on failure."""
    code, out, _ = connection.run(
        "curl -fsSL -m 20 -H 'Accept: application/vnd.github+json' " + _api(repo))
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
    return {"tag": data.get("tag_name") or data.get("name") or "",
            "name": apk.get("name"), "url": apk.get("browser_download_url"),
            "size": apk.get("size")}


def _cached_size(connection: Connection, path: str) -> Optional[int]:
    code, out, _ = connection.run(f"stat -c %s {path}")
    s = out.strip()
    return int(s) if code == 0 and s.isdigit() else None


def _written_size(connection: Connection, path: str) -> Optional[int]:
    code, out, _ = connection.run(f"wc -c < {path}")
    s = out.strip()
    return int(s) if code == 0 and s.isdigit() else None


def sync_app(app_key: str, connection: Connection, cache_dir: str = APPS_CACHE_DIR,
             force: bool = False) -> SyncResult:
    """Refresh one app's cached APK from its latest GitHub release when online.
    Offline -> clean skip. Downloads only when missing or a different size than the
    release asset; a size mismatch is discarded. Writes a ``.meta`` sidecar +
    ``.<app>_version`` marker so the serve layer knows what it has offline."""
    app = APPS.get(app_key)
    if app is None:
        return SyncResult(online=False, failed=[app_key],
                          message=f"Unknown app '{app_key}'.")
    if not app.get("free_redistribution"):
        return SyncResult(online=False, failed=[app_key],
                          message=f"{app['name']} may not be redistributed offline.")
    if not has_connectivity(connection):
        return SyncResult(online=False,
                          message=(f"Offline — using the {app['name']} already "
                                   "carried; install works without internet."))

    rel = _fetch_latest_release(connection, app["repo"])
    if not rel:
        return SyncResult(online=True,
                          message=f"Online, but couldn't read the latest {app['name']} release.")

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
            res.message = f"{app['name']} {rel['tag']}: download failed."
            return res
        if size is not None and _written_size(connection, path) != size:
            connection.run(f"rm -f {path}")
            res.failed.append(name)
            res.message = f"{app['name']} {rel['tag']}: download corrupt, discarded."
            return res
        res.changed.append(name)

    meta = {"app": app["name"], "key": app_key, "version": rel["tag"], "file": name,
            "license": app["license"], "source": url}
    connection.run(f"printf '%s' {_shq(json.dumps(meta))} > {path}.meta")
    connection.run(f"printf '%s' {_shq(rel['tag'])} > {cache_dir}/.{app_key}_version")
    res.message = (f"{app['name']} {rel['tag']}: "
                   + ("updated." if res.changed else "already current."))
    return res


def sync_all(connection: Connection, cache_dir: str = APPS_CACHE_DIR) -> dict:
    """Refresh every catalogue app; returns ``{app_key: SyncResult}``."""
    return {k: sync_app(k, connection, cache_dir) for k in APPS}


def cached_app(app_key: str, connection: Connection,
               cache_dir: str = APPS_CACHE_DIR) -> Optional[dict]:
    """The carried APK for one app, or None. ``{key, name, file, path, version,
    license, blurb}`` — the Comms screen's per-app row."""
    app = APPS.get(app_key)
    if app is None:
        return None
    code, out, _ = connection.run(f"ls -1 {cache_dir}/*.apk 2>/dev/null")
    if code != 0 or not out.strip():
        return None
    # match this app's apk by name-in-filename (columba*.apk / sideband*.apk)
    files = [f for f in out.strip().splitlines() if app_key in f.rsplit("/", 1)[-1].lower()]
    if not files:
        return None
    path = files[-1]
    vcode, vout, _ = connection.run(f"cat {cache_dir}/.{app_key}_version 2>/dev/null")
    return {"key": app_key, "name": app["name"], "file": path.rsplit("/", 1)[-1],
            "path": path, "version": vout.strip() if vcode == 0 and vout.strip() else None,
            "license": app["license"], "blurb": app["blurb"]}


def cached_apps(connection: Connection,
                cache_dir: str = APPS_CACHE_DIR) -> List[dict]:
    """Every catalogue app with its carried-status. Each entry always has the
    catalogue fields; ``carried``/``version``/``file`` reflect what's on disk."""
    out = []
    for key, app in APPS.items():
        c = cached_app(key, connection, cache_dir)
        out.append(c and {**c, "carried": True} or {
            "key": key, "name": app["name"], "file": None, "path": None,
            "version": None, "license": app["license"], "blurb": app["blurb"],
            "carried": False})
    return out
