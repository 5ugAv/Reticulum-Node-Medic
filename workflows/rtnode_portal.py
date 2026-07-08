"""RTNode-2400 captive-portal client (Type B WiFi/LoRa onboarding).

Implements the firmware's real portal contract (see
docs/RTNODE2400_INTEGRATION.md, section A):

  * AP ``RTNode-Setup`` (open), gateway ``10.0.0.1``, config server on TCP 80.
  * Form submits ``POST /save`` as ``application/x-www-form-urlencoded``.
  * A partial POST is fine — unspecified LoRa fields keep firmware defaults.
  * Units: ``freq`` = MHz decimal string (``915.125``); ``bw`` = Hz integer
    (``125000``); ``sf``/``cr``/``txp`` integers.
  * Success = HTTP 200 whose body says the device will reboot; the board then
    ``ESP.restart()``s ~3 s later and beacons ~30 s after boot.

The HTTP POST is injected so this is unit-testable without a live board (and
without the Pi having to join the AP). The default poster uses the stdlib.
"""

from __future__ import annotations

import subprocess
import urllib.parse
import urllib.request
from typing import Callable, Dict, Tuple

from node_profile import NodeProfile

PORTAL_HOST = "10.0.0.1"
PORTAL_PATH = "/save"
PORTAL_SSID = "RTNode-Setup"

#: Fields the operator supplies.
OPERATOR_FIELDS = ("node_name", "ssid", "psk")
#: LoRa fields the tool pre-fills with recommended values (overridable).
PREFILLED_FIELDS = ("freq", "bw", "sf", "cr", "txp")


def build_form(
    profile: NodeProfile,
    node_name: str = "",
    wifi_ssid: str = "",
    wifi_password: str = "",
    wifi_enabled: bool = None,
) -> Dict[str, str]:
    """Build the ``POST /save`` form.

    Recommended LoRa parameters come from ``profile.radio`` (so any Build-mode
    override flows through); node name + WiFi credentials are operator-supplied.
    WiFi is enabled automatically when credentials are given (``wifi_en``),
    unless *wifi_enabled* is set explicitly.
    """
    r = profile.radio
    if wifi_enabled is None:
        wifi_enabled = bool(wifi_ssid)
    return {
        # operator-supplied
        "node_name": node_name,
        "ssid": wifi_ssid,
        "psk": wifi_password,
        "wifi_en": "1" if wifi_enabled else "0",
        # recommended LoRa params (firmware units)
        "freq": f"{r.frequency_mhz}",              # MHz decimal string
        "bw": str(int(r.bandwidth_khz * 1000)),    # Hz integer
        "sf": str(r.spreading_factor),
        "cr": str(r.coding_rate),
        "txp": str(r.tx_power_dbm),
    }


def encode_form(form: Dict[str, str]) -> str:
    return urllib.parse.urlencode(form)


def _default_post(url: str, body: str, headers: Dict[str, str]) -> Tuple[int, str]:
    req = urllib.request.Request(
        url, data=body.encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return (resp.status, resp.read().decode("utf-8", "replace"))


def submit_form(
    form: Dict[str, str],
    host: str = PORTAL_HOST,
    post: Callable[[str, str, Dict[str, str]], Tuple[int, str]] = _default_post,
) -> Tuple[bool, str]:
    """POST the form to the portal. Returns ``(success, message)``.

    Success requires HTTP 200 with a body indicating the board will reboot —
    the firmware's confirmation before it ``ESP.restart()``s.
    """
    url = f"http://{host}{PORTAL_PATH}"
    body = encode_form(form)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    try:
        status, text = post(url, body, headers)
    except Exception as exc:  # transport failure = not on the AP / unreachable
        return (False, f"Could not reach the portal at {host}: {exc}")
    if status == 200 and "reboot" in text.lower():
        return (True, "Portal accepted the config; the board is rebooting.")
    return (False, f"Portal rejected the config (HTTP {status}).")


def _default_join_ap(ssid: str) -> Tuple[bool, str]:
    """Join an open AP with nmcli (the tool runs on a Pi 5)."""
    try:
        proc = subprocess.run(
            ["nmcli", "device", "wifi", "connect", ssid],
            capture_output=True, text=True, timeout=30)
        return (proc.returncode == 0, (proc.stdout or proc.stderr).strip())
    except Exception as exc:  # nmcli missing / no wifi / timeout
        return (False, str(exc))


def onboard(
    profile: NodeProfile,
    node_name: str,
    wifi_ssid: str,
    wifi_password: str,
    *,
    do_join: bool = True,
    join_ap: Callable[[str], Tuple[bool, str]] = _default_join_ap,
    post: Callable[[str, str, Dict[str, str]], Tuple[int, str]] = _default_post,
) -> Tuple[bool, str]:
    """End-to-end onboarding: join the ``RTNode-Setup`` AP, then POST the form.

    The AP-join and HTTP POST are injected so this is unit-testable without a
    radio. If the join fails we do NOT post (nothing to talk to).
    """
    form = build_form(profile, node_name, wifi_ssid, wifi_password)
    if do_join:
        joined, jmsg = join_ap(PORTAL_SSID)
        if not joined:
            return (False, f"Could not join '{PORTAL_SSID}': {jmsg}")
    return submit_form(form, host=PORTAL_HOST, post=post)
