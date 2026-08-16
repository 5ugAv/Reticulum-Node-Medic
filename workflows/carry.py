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
from typing import List, Optional

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
    ver = _first_line(connection, f"cat {RNODE_UPDATE_DIR}/.rnm_bundle_version 2>/dev/null")
    nver = _count(connection, f"{RNODE_UPDATE_DIR}/*/")
    out.append(CarryStatus(
        "rnode_firmware", "RNode firmware",
        "Without it the medic cannot flash a radio — its first job.",
        carried=bool(ver) or nver > 0,
        detail=(f"bundle {ver}" if ver else (f"{nver} version(s), no bundle marker"
                                             if nver else "nothing cached")),
        nbytes=_bytes(connection, RNODE_UPDATE_DIR)))

    # --- Phone apps ----------------------------------------------------------
    # The medic is the post office; the messaging happens on a phone. With no
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
    nmb = _count(connection, "~/reticulum-tool/assets/maps/*.mbtiles")
    mb = _bytes(connection, "~/reticulum-tool/assets/maps")
    out.append(CarryStatus(
        "map_tiles", "Offline map",
        "Without it placing and finding nodes has no map to work against.",
        carried=nmb > 0, toppable=False,
        detail=(f"{nmb} mbtiles" if nmb else "no offline map"), nbytes=mb))

    # --- Python wheels -------------------------------------------------------
    # These build a NODE's software stack with no PyPI. Version-matched to the
    # medic's own Python; they are shipped in the repo, not downloaded here.
    nwhl = _count(connection, "~/reticulum-tool/assets/packages/*.whl")
    out.append(CarryStatus(
        "wheels", "Python wheels",
        "Without them a node cannot be built offline — no PyPI in the field.",
        carried=nwhl > 0, toppable=False,
        detail=f"{nwhl} wheel(s)" if nwhl else "none",
        nbytes=_bytes(connection, "~/reticulum-tool/assets/packages")))

    # --- Firmware build toolchain -------------------------------------------
    # RTNode firmware is COMPILED on the medic, so the toolchain must be aboard.
    esp = _count(connection, "~/.arduino15/packages/esp32")
    out.append(CarryStatus(
        "build_toolchain", "Firmware build toolchain",
        "Without it the medic cannot compile RTNode firmware for a new board.",
        carried=esp > 0, toppable=False,
        detail="esp32 core present" if esp else "no esp32 core",
        nbytes=_bytes(connection, "~/.arduino15/packages")))

    return out


@dataclass
class CarryReport:
    """The result of a provisioning run or audit."""
    statuses: List[CarryStatus] = field(default_factory=list)
    topped_up: List[str] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)
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


def carry_all(connection: Connection, force: bool = False) -> CarryReport:
    """Fill every cache that can be filled unattended, then re-audit.

    Only firmware and phone apps are topped up here. The map area and the wheel
    set are human decisions — this reports them as gaps rather than guessing,
    because a wrong guess costs gigabytes and still leaves the operator without
    what they needed.

    The final statuses come from a FRESH audit after the downloads, so the
    report reflects the disk, not what the downloaders claimed.
    """
    from workflows.updater import has_connectivity, sync_firmware
    from workflows.phone_apps import sync_all as sync_apps

    rep = CarryReport(online=has_connectivity(connection))
    if not rep.online:
        rep.statuses = audit(connection)
        rep.message = ("Offline — nothing could be topped up. Connect the medic "
                       "and run this again BEFORE it goes anywhere.")
        return rep

    try:
        fw = sync_firmware(connection, force=force)
        (rep.topped_up if not fw.failed else rep.failed).append("RNode firmware")
    except Exception as exc:                                      # noqa: BLE001
        rep.failed.append(f"RNode firmware ({exc})")

    try:
        for key, res in sync_apps(connection).items():
            (rep.topped_up if not res.failed else rep.failed).append(f"app:{key}")
    except Exception as exc:                                      # noqa: BLE001
        rep.failed.append(f"phone apps ({exc})")

    rep.statuses = audit(connection)
    gaps = [s.name for s in rep.missing]
    rep.message = ("Ready to go — everything is aboard." if rep.ready else
                   "STILL MISSING: " + ", ".join(gaps))
    return rep
