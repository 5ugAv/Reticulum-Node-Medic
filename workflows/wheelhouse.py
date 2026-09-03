"""Cache the medic's Python dependencies as wheels for offline cloning.

A MITOSIS (clone) run has to install rns/lxmf/segno/kivy (+ their deps) on a fresh Pi
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


# --------------------------------------------------------------------------- #
# APT packages. Same job, different package manager.
# --------------------------------------------------------------------------- #

#: Debian packages the medic needs that pip cannot supply, cached as .deb so a
#: clone with no internet can still install them.
#:
#: WHY THIS EXISTS. The LUKS vault died because ``cryptsetup`` was in apt and
#: nowhere on the medic — a feature a clone could never enable
#: ([[offline-is-the-whole-point]]). Dire Wolf is heading for exactly the same
#: trap: it is the software modem that lets a salvaged handheld radio carry
#: Reticulum traffic, it is in Debian, and it is not carried. A keeper in a
#: remote community cannot apt-get it.
APT_PACKAGES = (
    # Software TNC. Turns a voice radio's audio into packets and back, and
    # presents KISS on TCP 8001 — which is exactly what Reticulum's
    # TCPClientInterface with kiss_framing connects to (upstream's own
    # soundmodem example).
    "direwolf",
    # Dire Wolf needs a sound device; the medic has HDMI audio only, so the
    # keeper adds a USB adapter. These are what ALSA needs to drive one.
    "alsa-utils",
)

DEB_CACHE = "~/reticulum-tool/assets/packages/debs"


def _uri_lines(packages) -> str:
    """Shell that turns ``apt-get --print-uris`` into ``url filename algo hash``.

    apt prints::

        'http://…/direwolf_1.7%2bdfsg-2_arm64.deb' direwolf_1.7+dfsg-2_arm64.deb 432292 MD5Sum:b44e…

    Two things caught earlier attempts. The URL is percent-encoded (``%2b`` for
    ``+``) while the filename field is NOT, so saving under the URL's basename
    produces a file the checksum line can never match — a verify step that
    silently checked nothing. And the digest apt offers here is **MD5Sum**, not
    SHA256, so a sed keyed on SHA256 matched no lines at all and downloaded
    nothing while reporting success.

    The algorithm is therefore read from the line rather than assumed.
    """
    names = " ".join(packages)
    return (f"apt-get install --print-uris -qq {names} 2>/dev/null"
            " | tr -d \"'\""
            ' | awk \'{split($4, a, ":"); print $1, $2, a[1], a[2]}\''
            " | grep -E '^https?://'")


def apt_download_command(packages=APT_PACKAGES, dest: str = DEB_CACHE) -> str:
    """Fetch *packages* AND THEIR DEPENDENCIES as .deb, WITHOUT root.

    ``apt-get -d install`` needs root — it locks apt's lists — and this medic's
    sudo is deliberately scoped (2026-08-02), so that route would mean widening
    root access in order to perform a download. ``--print-uris`` needs no
    privileges at all.

    NOTE what apt lists: only packages NOT already installed here. The cache
    reflects what THIS medic was missing, which for a clone off the same image
    is the right set — but it is not a universal bundle.

    INTEGRITY, NOT AUTHENTICITY. The hash comes down the same channel as the
    file, so this catches a truncated or corrupted download, not a hostile
    mirror. Real authenticity would mean checking Debian's signed Release file;
    the packages are fetched here while the medic is online and trusted, which
    is the same footing as any apt install on this box.
    """
    return (f"mkdir -p {dest} && cd {dest} && " + _uri_lines(packages) +
            " > .uris && while read -r u f algo h; do "
            '[ -s "$f" ] || wget -q -O "$f" "$u"; done < .uris')


def verify_debs_command(packages=APT_PACKAGES, dest: str = DEB_CACHE) -> str:
    """Check every cached .deb against the digest apt published for it.

    A truncated download would otherwise surface on a clone in the field, with
    no internet to fetch it again."""
    return (f"cd {dest} && " + _uri_lines(packages) +
            " > .uris && awk '{print $4 \"  \" $2}' .uris > .sums && "
            "if grep -q MD5Sum .uris; then md5sum -c .sums; "
            "else sha256sum -c .sums; fi")


def deb_count(connection: Connection, dest: str = DEB_CACHE) -> int:
    out = connection.run(f"ls {dest}/*.deb 2>/dev/null | wc -l")[1].strip()
    try:
        return int(out)
    except ValueError:
        return 0


def cache_debs(connection, packages=APT_PACKAGES, dest: str = DEB_CACHE,
               timeout: int = 900):
    """Cache the apt packages a clone cannot fetch for itself.

    Returns ``(ok, message)``. Requires internet. Needs NO root — see
    ``apt_download_command``.
    """
    connection.run(f"mkdir -p {dest}")
    code, out, err = connection.run(apt_download_command(packages, dest),
                                    timeout=timeout)
    if code != 0:
        return False, (f"apt download failed (needs internet and root): "
                       f"{(err or out)[-200:]}")
    count = deb_count(connection, dest)
    if count == 0:
        return False, "apt reported success but no .deb files landed."
    return True, (f"Cached {count} .deb packages ({', '.join(packages)} and "
                  f"their dependencies) for offline install.")


def install_debs_command(dest: str = DEB_CACHE) -> str:
    """Install everything cached, offline. ``|| true`` then a fix-up, because a
    bare dpkg run trips over install order and the second pass settles it."""
    return (f"dpkg -i {dest}/*.deb || true; "
            f"dpkg --configure -a || true")
