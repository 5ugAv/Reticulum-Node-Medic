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
    # the PTY driver behind every stock-RNode flash (transport/connection.py
    # run_interactive); the parent has Debian's copy, a clone had none and
    # BUILD failed — erasing the board on the way (reviewer, 2026-10-06)
    "python3-pexpect", "python3-ptyprocess",
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
    # Cuts power to one USB port: the only way to revive a hung board without
    # touching the plug (fragile ports). Node Medic 1 has it; a clone did not
    # (Node Medic 2, 2026-10-06).
    "uhubctl",
    # Programs the code runs that a fresh card lacks. Node Medic 1 had each
    # and Node Medic 2 (a clone) had none (parity sweep, 2026-10-08):
    # provisioning/link.py installs a new Pi node's key through sshpass;
    "sshpass",
    # provisioning/pi_usbboot.py boots a Pi as a USB card reader;
    "rpiboot",
    # diagnostics/reticulum_software.py reads a serial port's ACL;
    "acl",
    # diagnostics/system_health.py checks pip3, and git names a checkout's build.
    "python3-pip", "git",
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
DISPLAY_PACKAGES = ("cage", "libgl1", "xwayland", "libsdl2-2.0-0",
                    # Settings ▸ Fix colours (the DSI-scramble cure) shells it
                    "wlr-randr")
ALL_PACKAGES = APT_PACKAGES + DISPLAY_PACKAGES

#: Program -> the package that provides it, for every program the code runs
#: that a fresh Pi OS Lite card (assets/clone_base/dpkg_status) does not have.
#: tests/test_clone_parity.py holds the code to this list, so a tool the medic
#: starts calling cannot be left behind by the clone again.
PROGRAM_PACKAGES = {
    "sshpass": "sshpass", "rpiboot": "rpiboot", "getfacl": "acl",
    "setfacl": "acl", "pip3": "python3-pip", "git": "git",
    "wlr-randr": "wlr-randr", "uhubctl": "uhubctl", "direwolf": "direwolf",
    "gpsd": "gpsd", "gpspipe": "gpsd-clients", "cage": "cage",
    "aplay": "alsa-utils", "arecord": "alsa-utils", "amixer": "alsa-utils",
}

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

#: Everything a medic installed after it was imaged travels to its clones too
#: (keeper, 2026-10-09: "We shouldn't give the medic an option to miss
#: anything"). It is read from dpkg's own log and kept in a ledger in the
#: records, so a rotated system log cannot lose it; the ledger travels to every
#: clone, so a clone of a clone keeps the list. Only these stay behind:
PACKAGES_NEVER = {
    "wayvnc": "a remote-screen service a clone never asked for",
    "realvnc-vnc-server": "a remote-screen service a clone never asked for",
    "xrdp": "a remote-screen service a clone never asked for",
    "x11vnc": "a remote-screen service a clone never asked for",
    "tigervnc-standalone-server": "a remote-screen service a clone never asked for",
}
ADDED_LEDGER = "~/.reticulum-node-medic/packages_added.txt"
DPKG_LOGS = "/var/log/dpkg.log*"


def base_packages(status: str = CLONE_BASE_STATUS) -> set:
    """Every package a fresh card already has installed."""
    names, current = set(), None
    try:
        with open(status, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith("Package: "):
                    current = line.split(":", 1)[1].strip()
                elif (line.startswith("Status: ") and current
                      and line.rstrip().endswith(" installed")
                      and "not-installed" not in line):
                    names.add(current)
    except OSError:
        pass
    return names


def dpkg_log_installs(pattern: str = DPKG_LOGS) -> set:
    """Every package dpkg's own log says was installed on this machine."""
    import glob
    import gzip
    import re
    out = set()
    for path in glob.glob(pattern):
        opener = gzip.open if path.endswith(".gz") else open
        try:
            with opener(path, "rt", errors="replace") as fh:
                for line in fh:
                    m = re.match(r"\S+ \S+ install (\S+?)(?::\S+)? ", line)
                    if m:
                        out.add(m.group(1))
        except OSError:
            continue
    return out


def installed_now() -> set:
    """The packages dpkg has installed on this machine now."""
    import subprocess
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f=${Package} ${Status}\n"],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.SubprocessError):
        return set()
    return {l.split()[0] for l in out.splitlines() if l.endswith("install ok installed")}


def added_packages(logs: str = DPKG_LOGS, ledger: str = ADDED_LEDGER, base=None,
                   installed=None, write: bool = True) -> tuple:
    """What this medic installed after it was imaged and still has, with what
    its parent recorded, minus what every fresh card already has, the planned
    baseline lists and PACKAGES_NEVER. Updates the ledger."""
    path = os.path.expanduser(ledger)
    try:
        with open(path, encoding="utf-8") as fh:
            recorded = {l.strip() for l in fh if l.strip() and not l.startswith("#")}
    except OSError:
        recorded = set()
    have = installed_now() if installed is None else set(installed)
    names = (dpkg_log_installs(logs) | recorded) & have
    if write and names != recorded:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("# packages this medic installed after imaging; "
                         "they travel to every clone\n")
                fh.write("".join(f"{n}\n" for n in sorted(names)))
        except OSError:
            pass
    base = base_packages() if base is None else set(base)
    return tuple(sorted(names - base - set(ALL_PACKAGES) - set(PACKAGES_NEVER)))


def carried_package_names(deb_dir: str = DEB_CACHE) -> set:
    """The package names the store holds a planned .deb for, in any plan."""
    names = set()
    for list_name in list(PLAN_LISTS) + ["added"]:
        for f in planned_debs(list_name, deb_dir) or []:
            names.add(os.path.basename(f).split("_", 1)[0])
    return names


def ensure_added_plan(added, deb_dir: str = DEB_CACHE, connection=None) -> tuple:
    """Make sure every package in *added* has a planned .deb in the store,
    planning the set against a fresh card when the store falls short (that
    needs the internet). Returns ``(files to hand on, packages still not
    covered)``; the second is empty when nothing is missing."""
    if not added:
        return [], []
    short = sorted(set(added) - carried_package_names(deb_dir))
    if short:
        if connection is None:
            from transport.connection import LocalConnection
            connection = LocalConnection()
        code, _out, _err = connection.run(
            planned_download_command("added", tuple(added), deb_dir), timeout=1800)
        if code == 0:
            short = sorted(set(added) - carried_package_names(deb_dir))
    return (planned_debs("added", deb_dir) or []), short


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
        added = added_packages()
        if added:
            code, out, err = connection.run(
                planned_download_command("added", added, dest), timeout=timeout)
            if code != 0:
                return False, ("could not plan or fetch what this medic added "
                               f"since imaging ({', '.join(added[:6])}): "
                               f"{(err or out)[-200:]}")
            n = connection.run(f"wc -l < {dest}/added.list")[1].strip()
            made.append(f"added since imaging {n}")
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


