"""Pure presentation helpers for node health — no Kivy, so unit-testable and
reusable by both the UI and any text/report output."""

from __future__ import annotations

from typing import List


def beacon_lines(record) -> List[str]:
    """Plain-English health rows for a node's latest decoded beacon."""
    b = record.latest_beacon
    if b is None:
        return ["No health beacon received yet."]
    lines = [
        f"Firmware: {b.firmware_version}   Board: {b.board_label}",
        f"Uptime: {b.uptime_s}s   Free heap (min): {b.free_heap_kb} KB",
        f"WiFi: {'up' if b.wifi_up else 'down'}"
        + (f" ({b.wifi_rssi_dbm} dBm)" if b.wifi_up else "")
        + f"   LoRa: {'up' if b.lora_up else 'down'}",
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


def echo_annotation(last_seen_hours, last_echo_hours):
    """The muted ``echo 0.2h`` tag for a node whose last "hearing" was the
    transport replaying it, or ``None`` when no such tag may appear.

    An echo is a byte-identical copy of the node's last beacon re-emitted by
    rnsd from its announce cache — the mesh repeating the node's last words,
    not the node speaking. On 2026-08-21 a powered-off, battery-less board's
    row stayed green for hours because exactly those replays kept "seeing" it.
    So the tag exists to make weaker evidence LOOK weaker, and it is bound by
    two rules the callers must not soften:

      * it only appears when the echo is strictly FRESHER than the sighting —
        once the node itself has spoken, the stale replay is noise;
      * it is an annotation and nothing else — it feeds no status colour and
        never makes a row greener.
    """
    if last_seen_hours is None or last_echo_hours is None:
        return None
    try:
        seen, echo = float(last_seen_hours), float(last_echo_hours)
    except (TypeError, ValueError):
        return None
    if echo >= seen:
        return None                # the node itself spoke since the replay
    return f"echo {format_age(echo)}"


def seen_line(node) -> str:
    """The full SEEN text for one dashboard row dict — ``"SEEN 3.1h"``, or
    ``"SEEN 3.1h · echo 0.2h"`` when a replay is fresher than the sighting.
    Pure and Kivy-free so the honesty rules above are unit-testable; the
    StatBar widget renders the same two parts (echo_annotation decides for
    both) with the echo half in muted grey."""
    base = f"SEEN {format_age(node.get('last_seen_hours'))}"
    tag = echo_annotation(node.get("last_seen_hours"),
                          node.get("last_echo_hours"))
    return base if tag is None else f"{base} · {tag}"
