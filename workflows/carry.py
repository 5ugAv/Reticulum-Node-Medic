"""What the medic must CARRY to work where there is no internet.

The medic's job is building communication infrastructure where none exists. It
is handed to someone who will use it in a remote area, and it has to work there
— not degrade, *work*. Everything it needs must be aboard before it leaves.

The pattern throughout this project is **carry it, don't fetch it**: download
once while connected, serve locally forever. The machinery for that already
existed per-cache — ``updater.sync_firmware``, ``phone_apps.sync_all``, the map
tile downloader — but nothing composed them and nothing could answer the only
question that matters at the bench:

    "Is this medic ready to be handed to someone and taken somewhere with no
    signal?"

Before this module nobody could tell. The phone-app cache sat empty for weeks
and was only found by looking (2026-08-16), because a cache that was never
filled looks exactly like a cache that is full until you inspect it.

**Honesty rule for this module:** it reports what it can VERIFY on disk right
now, and it says plainly which gaps it cannot close by itself. A provisioning
report that overstates readiness is worse than none — it sends someone into the
field confident.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from transport.connection import Connection


@dataclass
class CarryStatus:
    """One carried thing, as found on disk."""
    key: str
    name: str
    why: str                       #: what breaks in the field without it
    carried: bool
    detail: str = ""               #: what was actually found
    nbytes: int = 0
    toppable: bool = True          #: can carry_all() fill this unattended?
    how: str = ""                  #: if not: the exact way to get it aboard


def _count(connection: Connection, pattern: str) -> int:
    code, out, _ = connection.run(f"ls -1 {pattern} 2>/dev/null | wc -l")
    s = out.strip()
    return int(s) if code == 0 and s.isdigit() else 0


def _bytes(connection: Connection, path: str) -> int:
    """Bytes used by *path*, or 0. ``du -sk`` so it works for files and dirs."""
    code, out, _ = connection.run(f"du -sk {path} 2>/dev/null | tail -1 | cut -f1")
    s = out.strip()
    return int(s) * 1024 if code == 0 and s.isdigit() else 0


def _first_line(connection: Connection, cmd: str) -> str:
    code, out, _ = connection.run(cmd)
    return out.strip().splitlines()[0].strip() if code == 0 and out.strip() else ""


def audit(connection: Connection) -> List[CarryStatus]:
    """Everything the medic must carry, and whether it is actually aboard.

    Reads the filesystem rather than any record of what was intended — a
    manifest can be right while the disk is empty, and the disk is what goes
    into the field.
    """
    from workflows.phone_apps import APPS_CACHE_DIR
    from workflows.updater import RNODE_UPDATE_DIR

    out: List[CarryStatus] = []

    # --- RNode firmware ------------------------------------------------------
    # Without this the medic cannot flash a radio, which is its first job.
    from workflows.updater import PINNED_FIRMWARE
    ver = _first_line(connection, f"cat {RNODE_UPDATE_DIR}/.rnm_bundle_version 2>/dev/null")
    nver = _count(connection, f"{RNODE_UPDATE_DIR}/*/")
    out.append(CarryStatus(
        "rnode_firmware", "RNode firmware",
        "Without it the medic cannot flash a radio — its first job.",
        carried=bool(ver) or nver > 0,
        detail=((f"bundle {ver} (pinned)" if ver == PINNED_FIRMWARE else f"bundle {ver}")
                if ver else (f"{nver} version(s), no bundle marker"
                             if nver else "nothing cached")),
        nbytes=_bytes(connection, RNODE_UPDATE_DIR)))

    # --- Phone apps ----------------------------------------------------------
    # The medic is the propagation node; the messaging happens on a phone. With no
    # APK aboard, a phone in the field cannot be given a messenger at all.
    napk = _count(connection, f"{APPS_CACHE_DIR}/*.apk")
    out.append(CarryStatus(
        "phone_apps", "Phone messaging apps",
        "Without them a phone in the field cannot be given a messenger.",
        carried=napk > 0,
        detail=f"{napk} APK(s)" if napk else "none — nothing to hand a phone",
        nbytes=_bytes(connection, APPS_CACHE_DIR)))

    # --- Map tiles -----------------------------------------------------------
    # Cannot be topped up unattended: which area to cache is a human decision,
    # and guessing wrong wastes hours and gigabytes.
    # WHERE THE MAP LIVES NOW: ~/.reticulum-node-medic/maps since 2026-08-27
    # (ui.map_tiles.MAPS_DIR). This counted the old assets/maps and told the
    # operator the map they had just watched download was MISSING.
    from ui.map_tiles import MAPS_DIR, LEGACY_MAPS_DIR
    nmb = (_count(connection, f"{MAPS_DIR}/*.mbtiles")
           or _count(connection, f"{LEGACY_MAPS_DIR}/*.mbtiles"))
    mb = _bytes(connection, MAPS_DIR) or _bytes(connection, LEGACY_MAPS_DIR)
    out.append(CarryStatus(
        "map_tiles", "Offline map",
        "Without it placing and finding nodes has no map to work against.",
        carried=nmb > 0, toppable=False,
        detail=(f"{nmb} mbtiles" if nmb else "no offline map"), nbytes=mb,
        how="Open MAPS with Wi-Fi on, go to the area the nodes will be in, "
            "and tap Download offline map."))

    # --- Python wheels -------------------------------------------------------
    # These build a NODE's software stack with no PyPI. Version-matched to the
    # medic's own Python, so pip downloads them ON the medic when the
    # wheelhouse is empty (workflows.wheelhouse, from carry_all).
    nwhl = _count(connection, "~/reticulum-tool/assets/packages/*.whl")
    out.append(CarryStatus(
        "wheels", "Python wheels",
        "Without them a node cannot be built offline — no PyPI in the field.",
        carried=nwhl > 0,
        detail=f"{nwhl} wheel(s)" if nwhl else "none",
        nbytes=_bytes(connection, "~/reticulum-tool/assets/packages")))

    # --- The clone's screen packages ----------------------------------------
    # A new medic installs its screen stack from carried .debs (the field has
    # no apt); without them the clone cannot finish (ledger #115).
    from workflows.wheelhouse import DEB_CACHE
    ndeb = _count(connection, f"{DEB_CACHE}/*.deb")
    out.append(CarryStatus(
        "debs", "Clone's screen packages (.deb)",
        "Without them a new medic cannot install its screen — the clone "
        "cannot finish.",
        carried=ndeb > 0,
        detail=f"{ndeb} package(s)" if ndeb else "none cached",
        nbytes=_bytes(connection, DEB_CACHE)))

    # --- The Pi OS image -----------------------------------------------------
    # THE MODULE'S OWN BLIND SPOT, found by an adversarial review 2026-08-16: this
    # audit checked five things and not the one without which no node can be built
    # at all. A medic missing the OS image was reported "Ready to go" — exactly the
    # failure this file's docstring warns about, applied to itself.
    #
    # Not toppable: which image variant to carry is a human decision, and it is a
    # ~500 MB download nobody should trigger by accident.
    from provisioning.pi_imager import IMAGE_CANDIDATES
    img = ""
    for cand in IMAGE_CANDIDATES:
        if _first_line(connection, f"ls -1 {cand} 2>/dev/null"):
            img = cand
            break
    out.append(CarryStatus(
        "os_image", "Raspberry Pi OS image",
        "Without it no SD card can be written — no node can be built at all.",
        carried=bool(img), toppable=False,
        detail=(img.rsplit("/", 1)[-1] if img else "no image carried"),
        nbytes=_bytes(connection, img) if img else 0,
        how="Download Raspberry Pi OS Lite (64-bit) on any computer and copy "
            "it onto the medic as ~/pi_os_lite.img.xz."))

    # --- Firmware build toolchain -------------------------------------------
    # RTNode firmware is COMPILED on the medic, so the toolchain must be aboard
    # — BOTH halves of it: the Arduino esp32 core and PlatformIO's packages
    # (the RTNode build is a pio run). Checking only the first called a medic
    # ready that could not build (2026-10-04).
    esp = _count(connection, "~/.arduino15/packages/esp32")
    pio = _count(connection, "~/.platformio/packages/*/")
    have = [n for n, ok in (("esp32 Arduino core", esp), ("PlatformIO packages", pio)) if ok]
    lack = [n for n, ok in (("esp32 Arduino core", esp), ("PlatformIO packages", pio)) if not ok]
    out.append(CarryStatus(
        "build_toolchain", "Firmware build toolchain",
        "Without it the medic cannot compile RTNode firmware for a new board.",
        carried=not lack, toppable=False,
        detail=(", ".join(have) + " present") if not lack else "missing: " + ", ".join(lack),
        nbytes=(_bytes(connection, "~/.arduino15/packages")
                + _bytes(connection, "~/.platformio/packages")),
        how="Fetched by the first firmware build the medic does while online "
            "— BUILD an RTNode board once with Wi-Fi on."))

    return out


@dataclass
class CarryReport:
    """The result of a provisioning run or audit."""
    statuses: List[CarryStatus] = field(default_factory=list)
    topped_up: List[str] = field(default_factory=list)     #: fetched this run
    checked: List[str] = field(default_factory=list)       #: already current
    failed: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)         #: e.g. a newer firmware NOT fetched
    online: bool = False
    message: str = ""

    @property
    def ready(self) -> bool:
        """True only if EVERY item is aboard. Deliberately strict: 'mostly
        provisioned' is the state that strands someone."""
        return all(s.carried for s in self.statuses)

    @property
    def missing(self) -> List[CarryStatus]:
        return [s for s in self.statuses if not s.carried]


def carry_all(connection: Connection, force: bool = False,
              progress: Optional[Callable[[str], None]] = None) -> CarryReport:
    """Fill every cache that can be filled unattended, then re-audit.

    Topped up here: RNode firmware, the phone apps, the clone's screen
    packages (.deb) when none are cached, and — when the wheelhouse
    is empty — the Python wheels (operator, 2026-10-04: the medic must be
    able to collect what it needs for itself while it is on Wi-Fi). The map
    area, the OS image and the build toolchain still need a human's hand or a
    different screen; each status says exactly where (``how``) rather than
    guessing. *progress* is told what is being fetched, step by step — a
    "Preparing…" button with nothing moving behind it reads as broken.

    The final statuses come from a FRESH audit after the downloads, so the
    report reflects the disk, not what the downloaders claimed. ``topped_up``
    is what was fetched, ``checked`` what was already current, ``failed`` what
    could not be fetched — and the message says which of the three happened,
    so a press with everything already aboard is not a silent no-op.
    """
    from workflows.updater import has_connectivity, sync_firmware
    from workflows.phone_apps import sync_all as sync_apps
    from workflows.wheelhouse import cache_wheels, wheel_count

    def say(text: str) -> None:
        if progress is not None:
            try:
                progress(text)
            except Exception:                                      # noqa: BLE001
                pass

    say("Checking for an internet connection…")
    rep = CarryReport(online=has_connectivity(connection))
    if not rep.online:
        rep.statuses = audit(connection)
        rep.message = ("Offline — nothing could be topped up. Connect the medic "
                       "and run this again BEFORE it goes anywhere.")
        return rep

    say("Checking RNode firmware against the latest release…")
    try:
        fw = sync_firmware(connection, force=force)
        if fw.failed:
            rep.failed.append("RNode firmware")
        elif fw.changed:
            rep.topped_up.append("RNode firmware"
                                 + (f" {fw.version}" if fw.version else ""))
        else:
            rep.checked.append("RNode firmware")
        if getattr(fw, "newer_available", None):
            # The pin (operator, 2026-10-04): said plainly, never acted on here.
            rep.notes.append(f"RNode firmware stays at {fw.version}: upstream "
                             f"has {fw.newer_available}, not fetched until it "
                             "has been benched.")
    except Exception as exc:                                      # noqa: BLE001
        rep.failed.append(f"RNode firmware ({exc})")

    say("Checking the phone messaging apps for newer releases…")
    try:
        for key, res in sync_apps(connection).items():
            if res.failed:
                rep.failed.append(f"{key} app")
            elif res.changed:
                rep.topped_up.append(f"{key} app")
            else:
                rep.checked.append(f"{key} app")
    except Exception as exc:                                      # noqa: BLE001
        rep.failed.append(f"phone apps ({exc})")

    try:
        if wheel_count(connection) == 0:
            say("Fetching the Python wheels a node is built from — a few minutes…")
            ok, msg = cache_wheels(connection)
            (rep.topped_up if ok else rep.failed).append(
                "Python wheels" if ok else f"Python wheels ({msg})")
        else:
            rep.checked.append("Python wheels")
    except Exception as exc:                                      # noqa: BLE001
        rep.failed.append(f"Python wheels ({exc})")

    try:
        from workflows.wheelhouse import DEB_CACHE, cache_debs, deb_count
        if deb_count(connection, DEB_CACHE) == 0:
            say("Fetching the clone's screen packages (.deb) — a minute or two…")
            ok, msg = cache_debs(connection)
            (rep.topped_up if ok else rep.failed).append(
                "clone's screen packages" if ok
                else f"clone's screen packages ({msg})")
        else:
            rep.checked.append("clone's screen packages")
    except Exception as exc:                                      # noqa: BLE001
        rep.failed.append(f"clone's screen packages ({exc})")

    say("Checking the disk again…")
    rep.statuses = audit(connection)
    gaps = [s.name for s in rep.missing]
    parts = []
    if rep.topped_up:
        parts.append("Fetched: " + ", ".join(rep.topped_up) + ".")
    if rep.failed:
        parts.append("Could not fetch: " + ", ".join(rep.failed) + ".")
    if rep.ready:
        parts.append("Ready to go — everything is aboard."
                     if (rep.topped_up or rep.failed) else
                     "Checked online just now — everything aboard is current; "
                     "nothing new to fetch.")
    else:
        parts.append("STILL MISSING: " + ", ".join(gaps))
    parts.extend(rep.notes)
    rep.message = " ".join(parts)
    return rep
