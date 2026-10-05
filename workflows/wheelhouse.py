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
    # The new medic's GPS: the Tracker streams NMEA and gpsd reads it. Pi OS
    # Lite has neither, so a clone's first job (set up the Tracker) needed
    # the internet (clone review, 2026-10-06).
    "gpsd",
    "gpsd-clients",
)

#: The clone's display stack. A Lite image cannot open a window (the Kivy
#: wheel's SDL has no kmsdrm driver); the proven cure is the `cage` Wayland
#: kiosk, ferried as .debs over the clone link because the field has no apt.
#: Those debs lived in a gitignored assets/debs that a medic built from
#: GitHub never has — so MITOSIS could not finish on such a medic (readiness
#: sweep, 2026-10-03). They are part of the one deb cache now.
#: NOT ONLY cage (first real clone, 2026-10-06): the app draws through libGL,
#: Xwayland and SDL2, and a clone without them crash-looped behind a "verified"
#: ladder. The whole screen set travels.
DISPLAY_PACKAGES = ("cage", "libgl1", "xwayland", "libsdl2-2.0-0")
ALL_PACKAGES = APT_PACKAGES + DISPLAY_PACKAGES

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
    # --reinstall: a package the PARENT medic already has installed is
    # otherwise left out of the list, and a fresh clone would then lack it
    # (alsa-utils on the bench, 2026-10-04: 7 URIs without, 8 with).
    return (f"apt-get install --reinstall --print-uris -qq {names} 2>/dev/null"
            " | tr -d \"'\""
            ' | awk \'{split($4, a, ":"); print $1, $2, a[1], a[2]}\''
            " | grep -E '^https?://'")


#: THE CLONE'S OWN STARTING POINT. The package list (/var/lib/dpkg/status) of
#: a fresh card this medic writes — Pi OS Lite, nothing installed yet — taken
#: from node-medic-2 before anything ran on it (Wi-Fi-off proof clone,
#: 2026-10-06). The cache used to be planned against what THIS medic runs,
#: which is months newer: it carried the newest cage without the graphics
#: library that cage needs (libwlroots-0.20), and newer system libraries the
#: card's older util-linux refused, so the screen would not install offline.
#: Planning against the card's own list makes apt name exactly what that card
#: needs, upgrades included. Maintainer/description fields are stripped.
CLONE_BASE_STATUS = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "assets", "clone_base", "dpkg_status")
#: The image that list came from (/etc/rpi-issue, first line).
CLONE_BASE_ISSUE = "Raspberry Pi reference 2026-06-18"
#: One planned list per install step on the clone, written next to the debs.
PLAN_LISTS = {"display": DISPLAY_PACKAGES, "radio": APT_PACKAGES}


def plan_uri_lines(packages, status: str = CLONE_BASE_STATUS) -> str:
    """``url filename algo hash`` for what a card in *status*'s state needs to
    install *packages* — new packages AND the upgrades they force, nothing
    else. No root: --print-uris with apt's lock checks off."""
    names = " ".join(packages)
    # An EMPTY archive cache: --print-uris leaves out any .deb already sitting
    # in this medic's own apt cache, and the plan silently lost
    # python3-matplotlib and python3-gi-cairo that way (gpsd-clients could
    # not install on the clone; dry run on node-medic-2, 2026-10-06).
    return (f"mkdir -p /tmp/nm-noarch/partial && "
            f"apt-get -o Dir::State::status={status} -o Debug::NoLocking=1 "
            f"-o Dir::Cache::archives=/tmp/nm-noarch/ "
            f"install --no-install-recommends --print-uris -qq {names} 2>/dev/null"
            " | tr -d \"'\""
            ' | awk \'{split($4, a, ":"); print $1, $2, a[1], a[2]}\''
            " | grep -E '^https?://'")


def planned_download_command(list_name: str, packages, dest: str = DEB_CACHE,
                             status: str = CLONE_BASE_STATUS) -> str:
    """Download one planned set, write ``<list_name>.list`` (the filenames the
    clone installs), and check every file against apt's digest. Fails if apt
    planned nothing — an unsolvable set prints no URIs at all."""
    plan = f".plan-{list_name}"
    return (f"mkdir -p {dest} && cd {dest} && " + plan_uri_lines(packages, status)
            + f" > {plan}; [ -s {plan} ] || {{ echo 'apt planned nothing for "
            f"{list_name}' >&2; exit 3; }}; "
            f"while read -r u f algo h; do [ -s \"$f\" ] || wget -q -O \"$f\" \"$u\"; "
            f"done < {plan} && awk '{{print $2}}' {plan} > {list_name}.list && "
            f"awk '{{print $4 \"  \" $2}}' {plan} > .sums-{list_name} && "
            f"if grep -q MD5Sum {plan}; then md5sum -c --quiet .sums-{list_name}; "
            f"else sha256sum -c --quiet .sums-{list_name}; fi")


def planned_debs(list_name: str, deb_dir: str):
    """The files ``<list_name>.list`` names, or None when there is no plan.
    An incomplete plan returns [] so the clone says the cache is short instead
    of installing half a set."""
    d = os.path.expanduser(deb_dir)
    path = os.path.join(d, f"{list_name}.list")
    try:
        with open(path) as fh:
            names = [l.strip() for l in fh if l.strip().endswith(".deb")]
    except OSError:
        return None
    files = [os.path.join(d, n) for n in names]
    return files if names and all(os.path.isfile(f) for f in files) else []


def apt_download_command(packages=ALL_PACKAGES, dest: str = DEB_CACHE) -> str:
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


def closure_packages_command(packages=ALL_PACKAGES) -> str:
    """Every package *packages* depend on, recursively — installed here or
    not. ``--print-uris`` alone lists only what THIS medic lacks (7 debs on
    2026-10-05), so a clone made with no internet could not finish its screen.
    Fed to ``apt_download_command`` with ``--reinstall`` this names the whole
    closure, and the clone's screen step installs from the cache alone."""
    pk = " ".join(packages)
    # apt-cache lists EVERY alternative of an either/or dependency (dbus-broker
    # AND dbus-daemon), and asking apt for both at once is a conflict, so the
    # tree is cut down to what this medic actually runs — a set that is known
    # to install together — plus the packages themselves.
    return (f"apt-cache depends --recurse --no-recommends --no-suggests "
            f"--no-conflicts --no-breaks --no-replaces --no-enhances {pk} "
            f"| grep -E '^[a-z0-9][a-z0-9.+-]*$' | sort -u > /tmp/nm-closure && "
            f"dpkg-query -W -f='${{Package}}\\n' | sort -u | comm -12 /tmp/nm-closure - ; "
            f"printf '%s\\n' {pk}")


def debs_for(packages, deb_dir: str, run=None) -> list:
    """The carried .deb FILES a clone needs for *packages*: their dependency
    closure (as this medic runs it), matched by package name. Installing the
    WHOLE cache dragged in stale extras whose own dependencies were not carried,
    and apt went to the internet for them (404s, first real clone, 2026-10-06).
    Runs on THIS medic, which has the apt metadata. Pure listing — no root."""
    import os
    import re
    import subprocess
    for list_name, members in PLAN_LISTS.items():
        if tuple(packages) == tuple(members):
            planned = planned_debs(list_name, deb_dir)
            if planned is not None:
                return planned          # planned against the clone's own card
    if run is None:
        # no shell: the two listings are run directly and joined here
        def run(_cmd):
            dep = subprocess.run(
                ["apt-cache", "depends", "--recurse", "--no-recommends",
                 "--no-suggests", "--no-conflicts", "--no-breaks",
                 "--no-replaces", "--no-enhances", *packages],
                capture_output=True, text=True)
            inst = subprocess.run(["dpkg-query", "-W", "-f=${Package}\n"],
                                  capture_output=True, text=True)
            tree = {l.strip() for l in dep.stdout.splitlines()
                    if re.fullmatch(r"[a-z0-9][a-z0-9.+-]*", l.strip())}
            have = {l.strip() for l in inst.stdout.splitlines() if l.strip()}
            return dep.returncode, "\n".join(sorted(tree & have))
    try:
        code, out = run(closure_packages_command(packages))
    except OSError:                       # no apt here (a dev machine): names only
        code, out = 1, ""
    names = {l.strip() for l in (out or "").splitlines() if l.strip()} | set(packages)
    d = os.path.expanduser(deb_dir)
    try:
        files = sorted(f for f in os.listdir(d) if f.endswith(".deb"))
    except OSError:
        return []
    return [os.path.join(d, f) for f in files if f.split("_", 1)[0] in names]


def offline_install_command(remote_dir: str) -> str:
    """Install the carried .debs in *remote_dir* with dpkg, no apt, no network.

    The set is planned complete against the card's own package list (see
    CLONE_BASE_STATUS), so dpkg has everything it needs and orders the unpack
    and configure itself. apt was tried first and failed twice on the
    Wi-Fi-off proof clone (2026-10-06): with its sources it reached for the
    internet ("Unable to fetch some archives"); without them, apt 3 stopped
    with "Internal Error, Pathname to install is not absolute" before
    unpacking anything. ``dpkg -i`` installed all 85 screen packages cleanly
    on node-medic-2, audit clean."""
    return f"dpkg -i {remote_dir}/*.deb"


def verify_debs_command(packages=ALL_PACKAGES, dest: str = DEB_CACHE) -> str:
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


def cache_debs(connection, packages=ALL_PACKAGES, dest: str = DEB_CACHE,
               timeout: int = 900, closure: bool = False):
    """Cache the apt packages a clone cannot fetch for itself.

    Returns ``(ok, message)``. Requires internet. Needs NO root — see
    ``apt_download_command``. *closure* fetches the packages' whole
    dependency tree (the keeper, 2026-10-06: "we should be able to do this
    offline"), not only what this medic happened to lack.
    """
    connection.run(f"mkdir -p {dest}")
    if os.path.isfile(CLONE_BASE_STATUS) and tuple(packages) == tuple(ALL_PACKAGES):
        made = []
        for list_name, members in PLAN_LISTS.items():
            code, out, err = connection.run(
                planned_download_command(list_name, members, dest), timeout=timeout)
            if code != 0:
                return False, (f"could not plan or fetch the {list_name} set for a "
                               f"fresh card: {(err or out)[-200:]}")
            n = connection.run(f"wc -l < {dest}/{list_name}.list")[1].strip()
            made.append(f"{list_name} {n}")
        if deb_count(connection, dest) == 0:
            return False, "apt reported success but no .deb files landed."
        return True, ("Planned against a fresh card (" + CLONE_BASE_ISSUE + "): "
                      + ", ".join(made) + " packages, every file checked.")
    if closure:
        code, out, err = connection.run(closure_packages_command(packages), timeout=120)
        names = [l.strip() for l in (out or "").splitlines() if l.strip()]
        if code != 0 or not names:
            return False, f"could not list the dependency closure: {(err or out)[-200:]}"
        packages = tuple(sorted(set(names) | set(packages)))
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


