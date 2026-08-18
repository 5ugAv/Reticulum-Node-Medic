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
        # SEEN ON A REAL PHONE, 2026-08-19: Google Play Protect refused this APK
        # with "Unsafe app blocked — this app was built for an older version of
        # Android and doesn't include the latest privacy protections". The APK
        # was the CURRENT official release, fetched from markqvist/Sideband
        # minutes earlier, so nothing the medic carries can avoid it: the
        # objection is to the targetSdkVersion upstream built with.
        #
        # It is said here because an operator handed a blocked install with no
        # warning reasonably concludes the medic gave them a broken file, and the
        # medic's whole promise is a working messenger with no internet to go and
        # check with. What is NOT claimed: the exact API level (the manifest
        # declares one; it was not read), or that Install anyway always appears —
        # on a new enough Android the refusal is the OS's and cannot be waved
        # through, which is why the alternative is named.
        "install_note": (
            "Android may refuse this one: \"built for an older version of "
            "Android\". That is how it was built upstream, not a bad download — "
            "this is the current official release. Tap More details for Install "
            "anyway; if that is not offered, use Columba instead."),
    },
}


def _api(repo: str) -> str:
    return f"https://api.github.com/repos/{repo}/releases/latest"


def _shq(s: str) -> str:
    """POSIX single-quote a string so it's safe inside a shell command."""
    return "'" + s.replace("'", "'\\''") + "'"


#: Build lines we must never hand a stranger. Columba v2.0.9 shipped 28 assets in
#: one release — an ``EXPERIMENTAL-reticulum-kt`` line beside an ``official-rns-py``
#: line — and the old "first name containing 'universal'" rule picked EXPERIMENTAL,
#: because that is what sorts first. Nobody chose that; alphabetical order did.
_APK_REJECT = ("experimental", "alpha", "beta", "nightly", "debug", "unsigned", "-rc")

#: Any-CPU build. An arch-split APK installs on some phones and not others, and the
#: medic cannot know which phone is about to walk up to it.
_APK_PREFER_ANY_CPU = "universal"

#: Crash reporting phones home to a remote service. A tool whose whole point is
#: working where there is no infrastructure, handed out by an operator who is not
#: traceable to the node, should not ship telemetry by default. Where a publisher
#: offers the choice, take the quiet build.
_APK_PREFER_QUIET = "no-sentry"


def _pick_apk(assets: list) -> Optional[dict]:
    """Choose the APK to carry, in this order of preference:

    1. a real ``.apk`` (``.aab`` is a Play Store bundle and will not install),
    2. a release build over a pre-release one (see ``_APK_REJECT``),
    3. an any-CPU ``universal`` build over an arch-split one,
    4. a build without bundled crash telemetry,
    5. the largest of whatever survives — arch-split releases list several and the
       fat one covers the most hardware.

    Each step only narrows when something survives it, so a release that offers no
    choice at all still yields its single APK."""
    def _narrow(cands, keep):
        kept = [a for a in cands if keep(str(a.get("name", "")).lower())]
        return kept or cands

    apks = [a for a in assets
            if isinstance(a, dict) and str(a.get("name", "")).lower().endswith(".apk")]
    if not apks:
        return None
    apks = _narrow(apks, lambda n: not any(t in n for t in _APK_REJECT))
    apks = _narrow(apks, lambda n: _APK_PREFER_ANY_CPU in n)
    apks = _narrow(apks, lambda n: _APK_PREFER_QUIET in n)
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


#: Never fill the card. These APKs are 100 MB and up, and the medic writing its own
#: root filesystem full is a brick in the field, not an inconvenience — SD corruption
#: on this hardware is already a known failure (see docs on overlayfs). Leave room.
_DISK_HEADROOM = 256 * 1024 * 1024


def _free_bytes(connection: Connection, path: str) -> Optional[int]:
    """Free bytes on the filesystem holding *path*, or None if it cannot be read.
    Asked over the connection, not with shutil, because the caller may be remote."""
    code, out, _ = connection.run(
        f"df -Pk {path} 2>/dev/null | tail -1 | awk '{{print $4}}'")
    s = out.strip()
    return int(s) * 1024 if code == 0 and s.isdigit() else None


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
        # Check BEFORE starting, not after. A download that runs the card out of
        # space fails late, having already written most of a 100 MB file, and
        # leaves the medic worse off than when it started.
        # FAIL CLOSED. This previously read `free is not None`, so an unreadable
        # df (busybox, an odd mount, a stat error) skipped the check entirely —
        # backwards for a guard whose whole purpose is not bricking the card.
        # "I could not measure the space" is a reason to stop, not to proceed.
        free = _free_bytes(connection, cache_dir)
        if size and free is None:
            res.failed.append(name)
            res.message = (f"{app['name']}: could not read free space on the card, "
                           "so nothing was downloaded. Check the medic's storage.")
            return res
        if size and free < size + _DISK_HEADROOM:
            res.failed.append(name)
            res.message = (
                f"{app['name']} {rel['tag']} needs {size // (1024 * 1024)} MB and "
                f"only {free // (1024 * 1024)} MB is free. Nothing downloaded — "
                "clear some space first.")
            return res
        # DOWNLOAD TO A TEMP NAME, RENAME ONLY ONCE VERIFIED.
        #
        # Two faults found together on 2026-08-16, and they compounded into the
        # worst possible field behaviour:
        #
        #  1. `curl -m 300` set curl's own limit, but Connection.run() defaults to
        #     timeout=30 (transport/connection.py:25) and the caller never passed
        #     one — so Python killed curl at 30s. A 100 MB APK cannot arrive in
        #     30s on any realistic connection, making a killed download the
        #     COMMON case, not the rare one.
        #  2. This branch returned without deleting the partial file, while the
        #     wrong-size branch below did delete. cached_app() only asks whether a
        #     filename matches — not whether the bytes are whole — so the fragment
        #     was reported as carried and the card offered "Send to my phone".
        #
        # A phone in the field would have been handed a truncated APK, with no
        # internet to recover. Writing to `.part` means an interrupted download
        # can never be mistaken for a finished one, whatever kills it.
        part = f"{path}.part"
        connection.run(f"rm -f {part}")
        if connection.run(f"curl -fsSL -m 300 -o {part} {url}", timeout=330)[0] != 0:
            connection.run(f"rm -f {part}")
            res.failed.append(name)
            res.message = (f"{app['name']} {rel['tag']}: download failed — nothing "
                           "was kept. Check the connection and try again.")
            return res
        if size is not None and _written_size(connection, part) != size:
            connection.run(f"rm -f {part}")
            res.failed.append(name)
            res.message = f"{app['name']} {rel['tag']}: download corrupt, discarded."
            return res
        connection.run(f"mv -f {part} {path}")
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
    # `ls` sorts lexicographically, so files[-1] is NOT the newest: with
    # columba-0.9 and columba-0.10 present, '1' < '9' puts 0.10 FIRST and the
    # stale 0.9 build wins. Trust the version marker the download wrote, and only
    # fall back to sort order when no marker names a file that is actually here.
    vcode, vout, _ = connection.run(f"cat {cache_dir}/.{app_key}_version 2>/dev/null")
    version = vout.strip() if vcode == 0 and vout.strip() else None
    path = files[-1]
    if version:
        tagged = [f for f in files if version.lstrip("v") in f]
        if tagged:
            path = tagged[-1]
    return {"key": app_key, "name": app["name"], "file": path.rsplit("/", 1)[-1],
            "path": path, "version": version,
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
