"""Build-mode warnings (89-91, 93).

These are not diagnostics — they are plain-English cautions the Build screen
shows the operator at the right moment (before flashing, before/after WiFi
setup). Kept as pure data so the UI and tests can consume them directly.
"""

from __future__ import annotations

from typing import List, Set

from node_profile import NodeHardware

BUILD_WARNINGS = [
    {
        "id": 89,
        "key": "usb_data_cable",
        "applies": "all",
        "text": "Use a USB DATA cable — not a charge-only cable. A charge-only "
                "cable powers the board but passes no data, so flashing and "
                "detection will silently fail.",
    },
    {
        "id": 90,
        "key": "antenna_band",
        "applies": "all",
        "text": "Confirm the antenna is rated for the 915 MHz band. A mismatched "
                "antenna (e.g. 433 MHz) can damage the radio when transmitting.",
    },
    {
        "id": 91,
        "key": "heltec_antenna_port",
        "applies": "heltec",
        "text": "On Heltec boards, connect the LoRa antenna to the LoRa port "
                "(not the Wi-Fi port). Transmitting without the LoRa antenna "
                "on the correct port can damage the radio.",
    },
    {
        "id": 93,
        "key": "captive_portal",
        "applies": "wifi",
        "text": "After entering the Wi-Fi credentials, dismiss the captive "
                "portal so the board can finish joining the network.",
    },
    {
        "id": 94,
        "key": "tracker_antenna_ports",
        "applies": "tracker",
        "text": "The Wireless Tracker has TWO tiny antenna sockets. Put the "
                "915 MHz LoRa antenna on the LoRa socket and the GPS antenna on "
                "the GNSS socket — mixing them up means no signal, and "
                "transmitting without the LoRa antenna can damage the radio.",
    },
    {
        "id": 95,
        "key": "gnss_open_sky",
        "applies": "tracker",
        "text": "The GPS antenna needs a clear view of the sky. Indoors or right "
                "up against a building, the Tracker may take a long time to find "
                "its location, or never find it. The first fix outdoors can take "
                "a few minutes.",
    },
    {
        "id": 96,
        "key": "gnss_ufl_silicone",
        "applies": "tracker",
        "text": "The GPS antenna's small U.FL socket on the board can work loose "
                "and fall off in the field. After connecting the antenna, add a "
                "small dab of neutral-cure silicone around the socket to hold it "
                "in place.",
    },
]


def warning_ids() -> Set[int]:
    return {w["id"] for w in BUILD_WARNINGS}


def warnings_for(hardware: NodeHardware, wifi: bool = False) -> List[dict]:
    """Return the warnings that apply to *hardware* (and Wi-Fi setup)."""
    result = []
    for w in BUILD_WARNINGS:
        applies = w["applies"]
        if applies == "all":
            result.append(w)
        elif applies == "heltec" and hardware is NodeHardware.HELTEC_V4:
            result.append(w)
        elif applies == "tracker" and hardware is NodeHardware.WIRELESS_TRACKER:
            result.append(w)
        elif applies == "wifi" and wifi:
            result.append(w)
    return result
