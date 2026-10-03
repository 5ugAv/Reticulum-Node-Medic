"""Pure presentation helpers for node health — no Kivy, so unit-testable and
reusable by both the UI and any text/report output."""

from __future__ import annotations

from typing import List


def toast_title(message: str, ok: bool, mode: str = None) -> str:
    """The title of a front-page toast, from what it is ABOUT. Every toast
    used to be "Home mode" when ok and "Mode change" otherwise — so a switch
    to Backpack opened a popup titled Home mode, and a low-battery warning one
    titled Mode change (readiness sweep, 2026-10-03)."""
    m = (message or "").lower()
    if "batter" in m:
        return "Battery"
    if "suppl" in m or "power" in m[:20]:
        return "Power"
    if "unreachable" in m or "needs a physical check" in m:
        return "Node down"
    if mode == "backpack" or m.startswith("backpack") or "switched to backpack" in m:
        return "Backpack mode"
    if mode == "home" or m.startswith("home mode"):
        return "Home mode"
    return "Mode change" if not ok else "Node Medic"


def format_duration(seconds) -> str:
    """Seconds as people read them: 42s · 12m 30s · 6h 2m · 3d 2h.

    "Uptime: 21631s" made the reader do the division (operator, 2026-10-03);
    sixty seconds is a minute, sixty minutes an hour, twenty-four hours a
    day. Two units at most — the third is noise at that scale."""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "?"
    if s < 0:
        s = 0
    d, rem = divmod(s, 86400)
    h, rem = divmod(rem, 3600)
    m, sec = divmod(rem, 60)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


def beacon_lines(record) -> List[str]:
    """Plain-English health rows for a node's latest decoded beacon.

    A Pi propagation node fills the ESP32-shaped beacon with what a Pi has:
    its reporter sends Wi-Fi RSSI 0 (not read), PSRAM False, reset 0,
    airtime-lock False and watchdog True BY DECLARATION, and clamps free RAM
    at the 16-bit cap. Printing those as readings — "WiFi: up (0 dBm)",
    "PSRAM: no", "Last reset: poweron", "Free heap 65535 KB" — was the
    medic stating things it never measured (skyfinger's page, 2026-10-03).
    A Pi gets the rows a Pi can answer; nothing declared is shown as read.
    """
    from monitor.health_beacon import BOARD_PI_PROPAGATION
    b = record.latest_beacon
    if b is None:
        return ["No health beacon received yet."]
    is_pi = b.board_id == BOARD_PI_PROPAGATION
    up = format_duration(b.uptime_s)
    # 0 on the wire is "no reading", never a real 0 dBm (2026-08-01 bug hunt)
    wifi = "WiFi: " + ("up" if b.wifi_up else "down")
    if b.wifi_up and b.wifi_rssi_dbm:
        wifi += f" ({b.wifi_rssi_dbm} dBm)"
    if is_pi:
        ram = (f"\u2265{(0xFFFF + 1) // 1024} MB" if b.free_heap_kb >= 0xFFFF
               else f"{b.free_heap_kb} KB")
        lines = [
            f"Firmware: {b.firmware_version}   Board: {b.board_label}",
            f"Uptime: {up}   Free RAM (min): {ram}",
            f"{wifi}   LoRa: {'up' if b.lora_up else 'down'}",
        ]
    else:
        lines = [
            f"Firmware: {b.firmware_version}   Board: {b.board_label}",
            f"Uptime: {up}   Free heap (min): {b.free_heap_kb} KB",
            f"{wifi}   LoRa: {'up' if b.lora_up else 'down'}",
        ]
    # Battery + power source (v2 nodes only — v1 nodes never reported it, so we
    # stay silent rather than print a misleading 'not reported').
    if b.has_power_telemetry:
        lines.append(f"Battery: {b.battery_label}   Power: {b.power_source_label}")
    # The node's own view of its LoRa link (how well it hears the mesh).
    if b.has_link_telemetry:
        link = []
        if b.lora_rssi_dbm is not None:
            link.append(f"{b.lora_rssi_dbm} dBm")
        if b.lora_snr_db is not None:
            link.append(f"SNR {b.lora_snr_db} dB")
        lines.append("LoRa link (node's view): " + "  ".join(link))
    if is_pi:
        # What the Pi reporter measures: an internet backbone interface in
        # its own rnstatus (2026-09-30) and the disk. Watchdog, PSRAM, reset
        # reason, airtime lock and the local TCP server are declared, not
        # read, on a Pi — so they are not shown as readings.
        lines += [
            f"Internet: {'up' if b.tcp_backbone_up else 'down'}",
            f"Disk: {'critically full' if b.fault else 'ok'}",
        ]
    else:
        lines += [
            f"Backbone TCP: {'up' if b.tcp_backbone_up else 'down'}   "
            f"Local TCP: {'up' if b.local_tcp_server_up else 'down'}",
            f"Watchdog: {'armed' if b.wdt_armed else 'NOT armed'}   "
            f"PSRAM: {'yes' if b.psram else 'no'}",
            f"Fault: {'YES' if b.fault else 'no'}   "
            f"Airtime lock: {'yes' if b.airtime_lock else 'no'}   "
            f"Last reset: {b.reset_reason_label}",
        ]
    return lines


def format_age(hours) -> str:
    """How long ago, in units a person can act on.

    Operator, 2026-08-10, looking at the VITALS list: "when it goes over 24
    hours let's call it days and hours — instead of saying SEEN 210 hours ago
    or 268 hours ago, that's hard to work out how many days that is."

    They are right, and the arithmetic is the whole point: a node last heard
    268 hours ago is a node that has been down for ELEVEN DAYS, and nobody
    reads that off "268h" without stopping to divide. The solar grace period
    that decides whether a quiet node is a fault is measured in days, so the
    display should be too.

    Under a day, hours stay — "3.2h" is already the right size of thought.
    """
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return "?"
    if h < 0:
        h = 0.0
    if h < 24:
        return f"{h:.1f}h"
    days = int(h // 24)
    rest = int(round(h - days * 24))
    if rest == 24:                      # 47.6h -> 2d, not 1d 24h
        days, rest = days + 1, 0
    return f"{days}d" if rest == 0 else f"{days}d {rest}h"


def format_age_fine(hours) -> str:
    """``format_age``, but below the hour it speaks MINUTES.

    Built for the echo tag: the canonical replay of the 2026-08-21 incident
    arrived 90 seconds after the board was unplugged, and through format_age
    that renders "echo 0.0h" — an annotation that says nothing. Minutes are
    the right size of thought under an hour; the floor is "1m" because an
    echo on record is never "0m ago" (that would read as "not there").
    format_age itself keeps its coarser scale — "3.2h" is already right for
    SEEN (operator, 2026-08-10), and its tests hold it to that.
    """
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return "?"
    if h < 0:
        h = 0.0
    if h < 0.95:
        return f"{max(1, int(round(h * 60)))}m"
    return format_age(h)


def seen_and_echo(row):
    """``(seen_text, echo_tag_or_None)`` for one dashboard row dict — THE one
    composer both surfaces render from (the VITALS StatBar strip and the node
    detail line), so the honesty rules live and are tested in exactly one
    place. An echo is rnsd replaying the node's last announce from its cache
    — the mesh repeating the node's last words, not the node speaking — the
    evidence that kept a powered-off, battery-less board green for hours on
    2026-08-21. So:

      * SEEN stays driven by ``last_seen_hours``, exactly as before;
      * the tag appears only while the echo is strictly FRESHER than the
        node's last DIRECT word (``last_direct_hours``). Not last_seen: a
        mesh scan may bump last_seen from a path row's learned-time (weaker
        evidence — see registry.ingest_mesh), and gating on that would hide
        the tag precisely when it mattered. No direct word on record means
        nothing outranks the echo, so the tag shows;
      * a rendered echo age identical to the rendered SEEN age is a
        difference below display resolution — not a meaningful claim — and
        is dropped;
      * the tag is a string and nothing more: it feeds no status colour, and
        the callers keep it muted.
    """
    # Honesty gate BEFORE any age math: a SEEN line is a freshness CLAIM, and
    # there are two states where there is no real forward measurement behind
    # it — both of which reach the 2026-08-21 dead-board-green class:
    #   * has_seen is explicitly False — a fleet row seeded by set_kin_roster
    #     that has NEVER been heard. "SEEN never", and no echo dresses it live.
    #   * seen_impossible — the freshest stamp predates the current clock (a
    #     backward step), so last_seen_hours clamped a negative age to 0.0,
    #     which format_age floors to "0.0h" and the theme paints GREEN. The
    #     age is unknowable, not zero: "SEEN ?" (the clock-step door,
    #     2026-08-22).
    # A MISSING has_seen key is a pre-flag caller, NOT a never-heard claim —
    # fall through to the numeric age (keeps the None-safe "SEEN ?").
    if row.get("has_seen") is False:
        return "SEEN never", None
    if row.get("seen_impossible"):
        return "SEEN ?", None
    seen_h = row.get("last_seen_hours")
    seen_text = f"SEEN {format_age(seen_h)}"
    echo_h = row.get("last_echo_hours")
    if echo_h is None:
        return seen_text, None
    direct_h = row.get("last_direct_hours")
    if direct_h is not None and echo_h >= direct_h:
        return seen_text, None      # the node itself has spoken since
    tag_age = format_age_fine(echo_h)
    if tag_age == format_age(seen_h):
        return seen_text, None      # below display resolution: no claim
    return seen_text, f"echo {tag_age}"


def seen_is_known(row) -> bool:
    """Whether the SEEN age is a REAL forward measurement — the colour gate
    for the SEEN icon. False for a never-heard row (``has_seen`` False) or an
    impossible reading that predates the clock (``seen_impossible``); those
    must render grey ("unknown"), NEVER green off an absent/clamped age (the
    2026-08-21 dead-board-green class, reached again through the clock-step
    door 2026-08-22). Kept beside seen_and_echo so text and colour agree in
    one tested place. A missing ``has_seen`` key is a pre-flag caller — treat
    the age as known, matching seen_and_echo's None-safe fall-through."""
    if row.get("has_seen") is False:
        return False
    if row.get("seen_impossible"):
        return False
    return True
