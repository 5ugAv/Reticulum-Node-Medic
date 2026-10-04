"""Self Diagnose — the medic checks (and heals) its OWN onboard radio/GPS board.

Born from the 2026-07-22 incident where a build flashed the medic's own radio
(Jonesey, the Heltec Wireless Tracker) instead of a work board and corrupted it.
The medic should be able to notice that and walk its way back — this is the pure,
tested diagnostic core the PROBE ▸ Self Diagnose screen drives.

Every check is a small pure function over injected command output (so it's unit
tested with no hardware); the runtime wires the real shell. A check returns a
``Finding`` with a severity, a human explanation, and — when we know a safe,
proven repair — a ``fix`` key the UI can offer.

Targets the ONBOARD board only (identified by its service-bound serial), NEVER a
work board — the whole reason the incident happened was a naive "first tty".
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional

#: NOT a serial — a placeholder, deliberately.
#:
#: A chip MAC is a permanent, unchangeable fingerprint of the board the
#: operator carries around a named city, and this file used to hardcode the
#: real one — while the anonymity rule elsewhere in this tool says a chip
#: MAC "identifies a place and a person". Each medic already records its OWN
#: boards by serial in its roster (ui/onboard_roster.py); the runtime reads
#: it from there and passes it in, which also makes this check correct on a
#: cloned medic whose boards are different.
ONBOARD_SERIAL = ""

SEV_OK = "ok"
SEV_WARN = "warning"
SEV_CRIT = "critical"


@dataclass
class Finding:
    check: str
    severity: str                 # ok | warning | critical
    detail: str
    fix: Optional[str] = None     # a repair key the UI can run, or None
    data: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.severity == SEV_OK


def check_usb_present(by_id_listing: str, serial: str = ONBOARD_SERIAL) -> Finding:
    """Is the onboard board enumerating on USB at all? (absent = unplugged, dead,
    or brown-out drop.) ``by_id_listing`` = the text of ``ls /dev/serial/by-id``.

    With no *serial* it says so and stops. "I could not check" is its own
    answer: a medic that has never commissioned its boards must not be told
    its radio has dropped off the bus."""
    if not serial:
        return Finding("usb_present", SEV_WARN,
                       "This medic has not recorded which board is its own, so "
                       "I cannot tell whether it is on USB. It learns its own "
                       "boards from the ones its services are bound to: run "
                       "scripts/setup_boot.sh with the medic's radio plugged in, "
                       "then restart Node Medic.")
    if serial.lower() in (by_id_listing or "").lower():
        return Finding("usb_present", SEV_OK, f"Onboard radio present on USB ({serial}).")
    return Finding("usb_present", SEV_CRIT,
                   f"Onboard radio ({serial}) is NOT on USB — it dropped off the bus. "
                   "Re-seat it / power-cycle the medic.", fix="usb_recover")


def check_chip_alive(esptool_output: str) -> Finding:
    """Even with dead firmware, the ESP32-S3 ROM bootloader answers esptool. If it
    does, the HARDWARE is fine and it's recoverable; if not, it's a cabling/power
    problem. ``esptool_output`` = stdout of an esptool chip_id/flash_id."""
    low = (esptool_output or "").lower()
    if "chip is esp32-s3" in low or ("mac:" in low and "esp32-s3" in low):
        return Finding("chip_alive", SEV_OK,
                       "Chip responds to esptool — hardware is fine, recoverable.")
    return Finding("chip_alive", SEV_CRIT,
                   "Chip did not answer esptool — check the USB DATA cable / port, "
                   "or hold BOOT while (re)plugging.", fix="usb_recover")


def check_firmware_provisioned(rnodeconf_i_output: str) -> Finding:
    """A provisioned RNode answers rnodeconf; a corrupt/unprovisioned one says
    'RNode did not respond' (often after a bogus 'Radio reporting frequency').
    ``rnodeconf_i_output`` = output of ``rnodeconf <port> -i``."""
    low = (rnodeconf_i_output or "").lower()
    if "did not respond" in low or "invalid response" in low or "no answer" in low:
        return Finding("firmware", SEV_CRIT,
                       "Firmware not answering as an RNode — corrupt or unprovisioned. "
                       "Reflash + re-provision the Tracker firmware.", fix="reflash_provision")
    if "reticulum" in low or "firmware version" in low or "device signature" in low:
        return Finding("firmware", SEV_OK, "RNode firmware present and provisioned.")
    return Finding("firmware", SEV_WARN,
                   "Couldn't confirm firmware state — re-check with the board settled.")


def check_splitter(is_active: bool, cpu_seconds: float, uptime_seconds: float,
                   recent_log: str = "") -> Finding:
    """The rnode-splitter feeds Jonesey's serial to rnsd + extracts GPS. A crash,
    or spinning at high CPU (reading garbage from dead firmware), means the radio
    path is broken. High CPU = cpu_seconds close to uptime."""
    if not is_active:
        return Finding("splitter", SEV_CRIT,
                       "rnode-splitter is not running — the radio path is down.",
                       fix="restart_splitter")
    busy = uptime_seconds > 0 and (cpu_seconds / uptime_seconds) > 0.5
    if busy:
        return Finding("splitter", SEV_WARN,
                       f"rnode-splitter is spinning hot ({cpu_seconds:.0f}s CPU in "
                       f"{uptime_seconds:.0f}s) — usually the board sending garbage "
                       "(dead firmware). Fix the firmware, then restart it.",
                       fix="restart_splitter")
    if re.search(r"traceback|SerialException|readiness to read", recent_log or "", re.I):
        return Finding("splitter", SEV_WARN,
                       "rnode-splitter logged a serial error recently — restart it.",
                       fix="restart_splitter")
    return Finding("splitter", SEV_OK, "rnode-splitter healthy.")


def check_rns_link(rns_recent_output: str) -> Finding:
    """The app's RNS loops 'Opening … Could not detect device' when the RNode
    interface can't sync — the classic dead-radio symptom. ``rns_recent_output`` =
    a recent slice of the app's RNS log/stdout."""
    if re.search(r"could not detect device", rns_recent_output or "", re.I):
        return Finding("rns_link", SEV_CRIT,
                       "RNS can't detect the RNode interface (retry loop) — the radio "
                       "isn't answering. Recover the firmware.", fix="reflash_provision")
    return Finding("rns_link", SEV_OK, "RNS radio interface is not erroring.")


def check_gps_fresh(gps_state_text: str, now: float, max_age_s: float = 600.0) -> Finding:
    """Does the medic know where it is — and can it be trusted?

    This used to report only whether the splitter's file was FRESH, which is a
    fact about the serial link and says nothing about position. It reported
    "Telemetry fresh" for months on a medic that had never once had a fix
    (2026-08-07), which is true and useless.

    It now separates the three things that are actually different, because each
    wants a different action:

      * the radio is not reporting GPS AT ALL  -> the firmware/board, not the sky
      * a LIVE fix                             -> we know where we are
      * a HELD fix                             -> COASTING on an old lock. The
        receiver still asserts a position while tracking zero satellites, so it
        LOOKS like an answer and is not one. This is the dangerous state and the
        only one that warrants a warning when the link is healthy.
      * no fix                                 -> legitimately normal indoors

    Demonstrated on hardware the day this was written: pointing the Tracker's
    ceramic patch at the sky gave 9 satellites and a lock in ~75 s; turning it
    to face the ground dropped satellites to 0 within a minute while the fix
    flag stayed 1 and the stale position kept being served.
    """
    try:
        st = json.loads(gps_state_text) if gps_state_text.strip() else {}
    except (ValueError, TypeError):
        return Finding("gps", SEV_WARN, "gps_state.json unreadable.")
    updated = st.get("updated") or 0
    age = now - updated
    if age > max_age_s:
        return Finding("gps", SEV_WARN,
                       f"GPS/radio telemetry is stale ({age/60:.0f} min old) — the "
                       "splitter isn't getting fresh frames from the board.",
                       data={"age_s": age})

    sats = st.get("sats") or 0
    has_fix = bool(st.get("has_fix"))
    frames = st.get("gps_frames")
    data = {"age_s": age, "sats": sats, "has_fix": has_fix, "gps_frames": frames}

    # The radio is talking, but never about GPS. That is a board/firmware
    # question, not a sky question — and telling someone to "go outside" here
    # wastes an afternoon on a receiver that was never going to answer.
    if frames == 0:
        return Finding("gps", SEV_WARN,
                       "The radio is reporting, but has never sent a GPS frame — "
                       "this firmware may not have GPS enabled. Not a sky problem.",
                       data=data)

    if has_fix and sats > 0:
        return Finding("gps", SEV_OK,
                       f"GPS live — {sats} satellites, position known.", data=data)

    if has_fix:
        # THE ONE THAT MATTERS. A position with no satellites behind it.
        return Finding("gps", SEV_WARN,
                       "GPS is COASTING — it still reports a position but is "
                       "tracking 0 satellites, so that position may be where "
                       "Node Medic WAS. Give the antenna a clear view of the sky.",
                       data=data)

    return Finding("gps", SEV_OK,
                   "No GPS fix yet (normal indoors) — the radio link is healthy.",
                   data=data)


def check_touch(verdict: str) -> Finding:
    """Which touch provider the start-up chose (ui.touch_input.choose, kept in
    /dev/shm/nodemedic-touch by main.py). "mtdev …" is the multitouch panel;
    any "mouse (…)" is the one-finger fallback — pinch-zoom and two-finger
    gestures do not work, and a clone with a different panel lands here
    silently unless it is said (ledger #97)."""
    v = (verdict or "").strip()
    if v.startswith("mtdev"):
        return Finding("touch", SEV_OK, f"Touch: multitouch ({v}).")
    if v.startswith("mouse"):
        reason = v[len("mouse"):].strip(" ()") or "fallback"
        return Finding("touch", SEV_WARN,
                       f"Touch: single-finger fallback — {reason}. Pinch-zoom "
                       "and two-finger gestures won't work on this panel.")
    return Finding("touch", SEV_WARN,
                   "Touch: no record of which input provider was chosen.")


def check_disk_space(df_output: str, warn_pct: int = 85, crit_pct: int = 95) -> Finding:
    """SD/root filesystem fullness from ``df -P /``. A full card fails writes and can
    corrupt the SD; parses the Use% column of the last data line."""
    pct = None
    for line in (df_output or "").splitlines():
        m = re.search(r"(\d+)%", line)
        if m:
            pct = int(m.group(1))
    if pct is None:
        # "Couldn't read it" is NOT "plenty of space". A medic whose df won't
        # parse must not show a green disk tick — same stance as
        # check_usb_present: an unknowable reading is a WARN, not an OK.
        return Finding("disk", SEV_WARN, "Could not read disk usage — unknown.")
    if pct >= crit_pct:
        return Finding("disk", SEV_CRIT,
                       f"Storage almost full ({pct}%) — writes may fail or corrupt the "
                       "SD card. Free space now.", fix="free_space", data={"pct": pct})
    if pct >= warn_pct:
        return Finding("disk", SEV_WARN,
                       f"Storage getting full ({pct}%). Clear some space soon.",
                       fix="free_space", data={"pct": pct})
    return Finding("disk", SEV_OK, f"Storage OK ({pct}% used).", data={"pct": pct})


def check_service(name: str, is_active: bool, critical: bool = True) -> Finding:
    """A core systemd service on the medic. rnsd down = the whole mesh stack is down."""
    if is_active:
        return Finding(f"service_{name}", SEV_OK, f"{name} is running.")
    sev = SEV_CRIT if critical else SEV_WARN
    return Finding(f"service_{name}", sev, f"{name} is NOT running — restart it.",
                   fix=f"restart_{name}")


#: Where the drop-in that keeps NetworkManager off the cable link belongs.
NM_USB0_CONF = "/etc/NetworkManager/conf.d/99-nodemedic-usb0.conf"


def check_cable_link_unmanaged(conf_present: bool, nm_log_tail: str = "") -> Finding:
    """Is NetworkManager leaving the cable-birth link alone?

    THE FAULT THIS CATCHES took an evening to find and was blamed on four
    innocent things first. When a Pi is plugged in for a cable birth it appears
    on the medic as usb0. NetworkManager invents a "Wired connection N" for it
    and runs DHCP — but the link is a static /29 the medic configures itself.
    So NM waits 45 s, fails with ip-config-unavailable, tears the interface
    down and starts again. That wipes the address the medic just set, and the
    thrashing wedges the gadget:

        cdc_ether usb0: NETDEV WATCHDOG: transmit queue 0 timed out

    It killed two builds on 2026-08-11 while being blamed on the power supply,
    the cable, the Pi, and two different medic USB ports — all of which were
    fine, and one of which was brand new.

    The node side has been immune since the card started marking usb0
    unmanaged. The MEDIC side never was, because the medic is "the tool" and
    nobody thinks of it as the other end of the same cable.
    """
    if conf_present:
        return Finding("cable_link_unmanaged", SEV_OK,
                       "NetworkManager leaves the cable-birth link alone.")
    saw_dhcp = "dhcp4 (usb0)" in (nm_log_tail or "")
    return Finding(
        "cable_link_unmanaged",
        SEV_CRIT if saw_dhcp else SEV_WARN,
        ("NetworkManager is running DHCP on usb0 — it will fail after 45 s, "
         "drop the link mid-build and wedge it."
         if saw_dhcp else
         "NetworkManager is not told to leave usb0 alone, so a cable birth can "
         "be dropped mid-build.")
        + f" Install {NM_USB0_CONF} (scripts/nodemedic-usb0-unmanaged.conf).",
        data={"conf": NM_USB0_CONF})


def check_cpu_temp(temp_output: str, warn_c: float = 75.0, crit_c: float = 82.0) -> Finding:
    """Pi SoC temperature from ``vcgencmd measure_temp`` ('temp=48.3''C'). The Pi
    throttles around 80-85C; sustained heat drops performance and ages the board."""
    m = re.search(r"temp=([\d.]+)", temp_output or "")
    if not m:
        # A thermometer we can't read is not a cool CPU. Like check_usb_present,
        # an unknowable reading is a WARN — never a green ":) healthy" on a medic
        # that may be overheating while browning out.
        return Finding("cpu_temp", SEV_WARN,
                       "Could not read CPU temperature — unknown.")
    t = float(m.group(1))
    if t >= crit_c:
        return Finding("cpu_temp", SEV_CRIT,
                       f"CPU running hot ({t:.0f}C) — it will throttle. Improve airflow "
                       "/ cooling.", data={"temp_c": t})
    if t >= warn_c:
        return Finding("cpu_temp", SEV_WARN, f"CPU warm ({t:.0f}C) — watch cooling.",
                       data={"temp_c": t})
    return Finding("cpu_temp", SEV_OK, f"CPU temperature fine ({t:.0f}C).",
                   data={"temp_c": t})


def check_throttled(throttled_output: str) -> Finding:
    """``vcgencmd get_throttled`` bits. Active (now) under-voltage/throttle is a power
    or heat problem happening NOW; the 'occurred' bits mean it happened since boot —
    important for a battery/UPS-powered medic."""
    m = re.search(r"0x([0-9a-fA-F]+)", throttled_output or "")
    if not m:
        # THE browning-out case this whole audit is about: the medic cannot read
        # its own throttle register. That is precisely when a green power tick is
        # most dangerous, so — like check_usb_present — report the unknowable as
        # a WARN, not an OK.
        return Finding("power", SEV_WARN,
                       "Could not read power/throttle status — unknown.")
    bits = int(m.group(1), 16)
    if bits & 0x1 or bits & 0x4:                 # under-voltage now / throttled now
        why = "under-voltage" if bits & 0x1 else "throttling"
        return Finding("power", SEV_CRIT,
                       f"Active {why} — the 5V supply can't keep up. Use a stronger "
                       "supply / better cable.", data={"throttled": hex(bits)})
    if bits & 0x10000 or bits & 0x40000:         # occurred since boot
        return Finding("power", SEV_WARN,
                       "Under-voltage/throttling happened earlier — keep an eye on the "
                       "power supply.", data={"throttled": hex(bits)})
    return Finding("power", SEV_OK, "Power stable (no under-voltage/throttle).",
                   data={"throttled": hex(bits)})


def check_wifi(nmcli_output: str, warn_pct: int = 40) -> Finding:
    """The medic's own WiFi from ``nmcli -t -f IN-USE,SIGNAL,SSID dev wifi`` — the
    active AP (line starting ``*``) carries a 0-100 SIGNAL percentage. Weak WiFi
    slows updates + serving apps; NEVER critical (the medic works offline)."""
    active = next((ln for ln in (nmcli_output or "").splitlines()
                   if ln.startswith("*")), None)
    if not active:
        return Finding("wifi", SEV_OK, "WiFi not connected (offline is fine).")
    parts = active.split(":", 2)
    try:
        pct = int(parts[1])
    except (IndexError, ValueError):
        # We ARE connected but couldn't parse the signal strength — that is
        # unknowable, not fine. (Being OFFLINE is fine and stays OK above; this
        # branch is a connected AP with an unreadable reading.) WARN, matching
        # check_usb_present's "couldn't check is its own answer".
        return Finding("wifi", SEV_WARN,
                       "Could not read WiFi signal strength — unknown.")
    ssid = parts[2] if len(parts) > 2 else ""
    label = f" to {ssid}" if ssid else ""
    if pct <= warn_pct:
        return Finding("wifi", SEV_WARN,
                       f"WiFi is weak ({pct}%{label}) — updates and serving apps may "
                       "be slow.", data={"signal_pct": pct})
    return Finding("wifi", SEV_OK, f"WiFi OK ({pct}%{label}).", data={"signal_pct": pct})


def check_clock_sync(timedatectl_output: str, now: float,
                     min_epoch: float = 1704067200.0,
                     last_sync: "Optional[float]" = None,
                     last_sync_source: str = "",
                     fresh_s: float = 24 * 3600.0) -> Finding:
    """System-clock health. A field medic with no RTC battery boots to a bogus time
    after a power loss — which breaks certificate dates, LXMF timestamps, TLS, and
    the outage-watch/beacon timing. Critical if the clock reads before 2024 (never
    synced); a warning if it just isn't auto-syncing (NTP off). ``timedatectl show``
    provides ``NTPSynchronized=yes|no``."""
    if now < min_epoch:
        return Finding("clock", SEV_CRIT,
                       "System clock is WRONG (reads before 2024) — it didn't sync "
                       "after boot. Set it in Settings > Date & time (GPS or NTP). "
                       "A wrong clock breaks certs, messaging and mesh timing.",
                       fix="sync_clock", data={"epoch": now})
    out = timedatectl_output or ""
    if re.search(r"NTPSynchronized=yes", out):
        return Finding("clock", SEV_OK, "System clock synced (NTP).")
    # OFFLINE IS NORMAL for this medic: with no server in reach NTPSynchronized
    # stays "no" for ever and the GPS is the clock's source, so a fresh sync
    # stamp (GPS or a manual set) is a synced clock — "NTP off" was a false
    # warning on every healthy field medic (readiness ledger #92).
    if last_sync is not None and 0 <= now - last_sync <= fresh_s:
        age_min = int((now - last_sync) // 60)
        src = last_sync_source or "GPS"
        return Finding("clock", SEV_OK,
                       f"Clock set from {src} {age_min} min ago (offline — no NTP needed).")
    if re.search(r"(^|\n)NTP=no", out):
        return Finding("clock", SEV_WARN,
                       "Clock isn't auto-syncing (NTP off) and the GPS hasn't set it. "
                       "If it drifts, timestamps and certs can break — turn auto-sync "
                       "on in Settings > Date & time, or give the GPS a view of the sky.")
    return Finding("clock", SEV_WARN,
                   "No time source has reached the clock yet: NTP is on but no server "
                   "has answered, and the GPS hasn't set it. Give the GPS a view of the "
                   "sky, or get online once.")


def check_rns_responding(rnstatus_output: str) -> Finding:
    """``rnstatus`` talks to the running rnsd shared instance and lists interfaces.
    'service active' (systemd) doesn't prove rnsd actually WORKS — this does: if
    rnstatus can't reach it the mesh stack is effectively down; if it answers but no
    interface is Up, the radio/network links are the problem."""
    low = (rnstatus_output or "").lower()
    # "I COULD NOT RUN THE TOOL" IS NOT "THE MESH IS DOWN".
    #
    # rnstatus lives in ~/.local/bin (pip --user), which a non-login subprocess
    # does not have on PATH — so the runner handed this check the text
    # "[errno 2] no such file or directory: 'rnstatus'", it matched "no such",
    # and PROBE reported the mesh stack down on a medic that was hearing
    # announces and talking to nodes over LoRa at that moment (2026-08-11).
    #
    # The two are different findings and want different actions: one is "restart
    # rnsd", the other is "the medic cannot find its own tools". Reporting the
    # first for the second teaches the operator to distrust the screen.
    if "errno 2" in low or "no such file or directory" in low:
        return Finding("rns", SEV_WARN,
                       "Could not run rnstatus, so rnsd's state is unknown — this "
                       "is not evidence that the mesh is down. rnstatus lives in "
                       "~/.local/bin; the medic is looking somewhere else.",
                       data={"unrunnable": True})
    if not low.strip() or "could not connect" in low or "connection refused" in low:
        return Finding("rns", SEV_CRIT,
                       "rnsd isn't responding (rnstatus can't reach it) — the mesh "
                       "stack is down. Restart it.", fix="restart_rnsd")
    up = len(re.findall(r"status\s*:?\s*up", low))
    if up == 0:
        return Finding("rns", SEV_WARN,
                       "rnsd is up but no interfaces are Up — check the radio / network "
                       "interfaces.", data={"interfaces_up": 0})
    return Finding("rns", SEV_OK, f"rnsd responding, {up} interface(s) up.",
                   data={"interfaces_up": up})


def check_lxmd(is_active: bool, wants_propagation: bool) -> Finding:
    """lxmd (the LXMF propagation node — stores messages for offline users) is only
    expected when the medic is a HOME propagation node. In backpack / transport-only
    it's legitimately off, so this only warns when propagation is wanted but lxmd
    isn't running (never a false alarm in backpack)."""
    if is_active:
        return Finding("lxmd", SEV_OK, "lxmd (message store-and-forward) running.")
    if wants_propagation:
        return Finding("lxmd", SEV_WARN,
                       "Propagation is on but lxmd isn't running — messages aren't "
                       "being stored for offline users. Restart it.", fix="restart_lxmd")
    return Finding("lxmd", SEV_OK, "lxmd off (not a propagation node — fine).")


def summarize(findings: List[Finding]) -> dict:
    """Roll up findings for the screen: worst severity + the ordered fix list."""
    crit = [f for f in findings if f.severity == SEV_CRIT]
    warn = [f for f in findings if f.severity == SEV_WARN]
    fixes = []
    for f in findings:                       # de-duped, in check order
        if f.fix and f.fix not in fixes:
            fixes.append(f.fix)
    worst = SEV_CRIT if crit else (SEV_WARN if warn else SEV_OK)
    return {"worst": worst, "critical": len(crit), "warning": len(warn),
            "fixes": fixes, "healthy": worst == SEV_OK}
