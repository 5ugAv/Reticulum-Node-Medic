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
import time
import urllib.parse
import urllib.request
from typing import Callable, Dict, Optional, Tuple

from node_profile import NodeProfile
from monitor.geo import format_coord, read_gps

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
    lat: float = None,
    lon: float = None,
    advertise: bool = True,
    jitter: bool = True,
) -> Dict[str, str]:
    """Build the ``POST /save`` form.

    Recommended LoRa parameters come from ``profile.radio`` (so any Build-mode
    override flows through); node name + WiFi credentials are operator-supplied.
    WiFi is enabled automatically when credentials are given (``wifi_en``),
    unless *wifi_enabled* is set explicitly.

    If *lat*/*lon* are supplied (the Pi's GPS fix at the node) the node is set
    to advertise its location — with jitter ON by default, so the firmware
    publishes a ~800 m privacy-fuzzed pin to the public map while keeping the
    exact coordinates in its own config. With no coordinates, advertisement is
    left OFF (never write 0,0).
    """
    r = profile.radio
    if wifi_enabled is None:
        wifi_enabled = bool(wifi_ssid)
    form = {
        # operator-supplied
        "node_name": node_name,
        "ssid": wifi_ssid,
        "psk": wifi_password,
        "wifi_en": "1" if wifi_enabled else "0",
        # --- project-standard toggles: every RTNode-2400 built for this
        # deployment is configured the same way, so nodes come out consistent ---
        # Local TCP server ON at :4242 — makes the node reachable over the LAN
        # (this is the "enabling" step; it's how the Monitor polls the node, and
        # how an rnsd elsewhere connects to it, e.g. FAITH:4242).
        "ap_tcp_en": "1",
        "ap_tcp_port": "4242",
        # mDNS discovery ON; mdns_name deliberately left unset so the firmware
        # picks its own default name.
        "mdns_en": "1",
        # No TCP backbone (bb_host/bb_port left unset) and no IFAC on the LoRa
        # interface — this build runs standalone by design.
        "tcp_mode": "0",
        "ifac_en": "0",
        # recommended LoRa params (firmware units)
        "freq": f"{r.frequency_mhz}",              # MHz decimal string
        "bw": str(int(r.bandwidth_khz * 1000)),    # Hz integer
        "sf": str(r.spreading_factor),
        "cr": str(r.coding_rate),
        "txp": str(r.tx_power_dbm),
    }
    if advertise and lat is not None and lon is not None:
        form["advert_en"] = "1"
        form["advert_lat"] = format_coord(lat)
        form["advert_lon"] = format_coord(lon)
        form["advert_jitter"] = "1" if jitter else "0"
    else:
        form["advert_en"] = "0"                    # off, not 0,0
    return form


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


def join_ap_commands(ssid: str):
    """The nmcli commands to join an open AP as the medic. Both verified on real
    hardware (Pi 5 provisioning an RTNode over WiFi):

    * **sudo** — NetworkManager blocks connection *activation* via polkit for the
      login user ("Not authorized to control networking"); the medic has
      passwordless sudo. (`nmcli device wifi connect` without it fails.)
    * **directed rescan first** — a plain repeated ``--rescan yes`` gets throttled
      to a stale empty list, so NM won't list the board's AP; a rescan for the
      *exact* SSID surfaces it (the AP is strong — measured -28 dBm on ch 1 — the
      issue was discovery, not signal).
    """
    return [
        ["sudo", "-n", "nmcli", "device", "wifi", "rescan", "ssid", ssid],
        ["sudo", "-n", "nmcli", "device", "wifi", "connect", ssid],
    ]


def _default_join_ap(ssid: str, attempts: int = 3,
                     sleep: Callable[[float], None] = time.sleep) -> Tuple[bool, str]:
    """Join an open AP with nmcli (the tool runs on a Pi 5). Rescans for the SSID
    then connects, retrying so a first throttled scan doesn't lose the AP."""
    rescan, connect = join_ap_commands(ssid)
    last = "not attempted"
    for _ in range(max(1, attempts)):
        try:
            subprocess.run(rescan, capture_output=True, text=True, timeout=20)
            sleep(3)  # let NM's directed scan complete before we connect
            proc = subprocess.run(connect, capture_output=True, text=True,
                                  timeout=30)
            if proc.returncode == 0:
                return (True, (proc.stdout or proc.stderr).strip())
            last = (proc.stderr or proc.stdout).strip()
        except Exception as exc:  # nmcli missing / no wifi / timeout
            last = str(exc)
    return (False, last)


def _nmcli(argv) -> str:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""


def medic_wifi_credentials(run: Callable[[list], str] = _nmcli) -> Tuple[str, str]:
    """The medic's OWN active WiFi (ssid, psk) — the network a built node should
    join (so the medic can reach it over the LAN) and the one to rejoin after the
    AP hop. Reads nmcli (psk needs sudo). Returns ("", "") if unavailable."""
    name = ""
    for line in (run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show",
                      "--active"]) or "").splitlines():
        if "wireless" in line:
            name = line.split(":")[0]
            break
    if not name:
        return ("", "")
    ssid = (run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show",
                 name]) or "").strip()
    psk = (run(["sudo", "-n", "nmcli", "-s", "-g", "802-11-wireless-security.psk",
                "connection", "show", name]) or "").strip()
    return (ssid, psk)


def rejoin_medic_wifi(ssid: str, run: Callable[[list], str] = _nmcli) -> bool:
    """Rejoin the medic's own WiFi after the AP hop. NM usually auto-reconnects a
    saved network, but we ask explicitly to be sure the medic comes back online."""
    if not ssid:
        return False
    run(["sudo", "-n", "nmcli", "device", "wifi", "rescan", "ssid", ssid])
    out = run(["sudo", "-n", "nmcli", "device", "wifi", "connect", ssid])
    return "successfully" in (out or "").lower() or "activated" in (out or "").lower()


def provision_node(
    profile: NodeProfile,
    node_name: str,
    medic_ssid: str,
    medic_psk: str,
    *,
    lat: float = None,
    lon: float = None,
    join_ap: Callable[[str], Tuple[bool, str]] = _default_join_ap,
    post: Callable[[str, str, Dict[str, str]], Tuple[int, str]] = _default_post,
    rejoin: Callable[[str], bool] = None,
    ap_ssid: str = PORTAL_SSID,
) -> Tuple[bool, str]:
    """Auto-provision a freshly-flashed RTNode over its ``RTNode-Setup`` AP: build
    the ``/save`` form (node name + the MEDIC's own WiFi so the node joins the same
    LAN + our LoRa params + fuzzed location), join the AP, POST, then ALWAYS rejoin
    the medic's WiFi (even on failure) so the medic never strands itself offline.
    Returns ``(ok, human_message)``. All I/O injected for tests."""
    rejoin = rejoin or rejoin_medic_wifi
    form = build_form(profile, node_name=node_name, wifi_ssid=medic_ssid,
                      wifi_password=medic_psk, lat=lat, lon=lon)
    joined, jmsg = join_ap(ap_ssid)
    if not joined:
        rejoin(medic_ssid)                         # make sure we're still online
        return (False, f"Could not join {ap_ssid} to configure the node: {jmsg}")
    try:
        ok, msg = submit_form(form, post=post)
    finally:
        rejoin(medic_ssid)                         # ALWAYS restore the medic's WiFi
    if ok:
        return (True, f"Node configured as '{node_name or 'RTNode'}' and joined "
                      f"{medic_ssid}; it reboots and beacons in ~30 s.")
    return (False, msg)


def onboard(
    profile: NodeProfile,
    node_name: str,
    wifi_ssid: str,
    wifi_password: str,
    *,
    lat: float = None,
    lon: float = None,
    gps_reader: Callable[[], Optional[Tuple[float, float]]] = None,
    confirm_location: Callable[[float, float],
                               Optional[Tuple[float, float]]] = None,
    do_join: bool = True,
    join_ap: Callable[[str], Tuple[bool, str]] = _default_join_ap,
    post: Callable[[str, str, Dict[str, str]], Tuple[int, str]] = _default_post,
) -> Tuple[bool, str]:
    """End-to-end onboarding: capture location, join ``RTNode-Setup``, POST.

    Location step (the node is "born" where the Pi is standing): unless explicit
    *lat*/*lon* are given, capture the Pi's own GPS via *gps_reader*, then hand
    the coordinates to *confirm_location(lat, lon)* so the operator can accept
    them as-is or edit them (e.g. a poor fix, or provisioning off-site). The
    callback returns the accepted ``(lat, lon)`` — or ``None`` to skip the
    advertisement. The advertised location is privacy-fuzzed (~800 m) by the
    firmware; the exact coordinates belong in the birth certificate. With no
    fix / no confirmation, advertisement is left off (never 0,0).

    The GPS read, AP-join and HTTP POST are all injected so this is unit-testable
    without a radio. If the join fails we do NOT post (nothing to talk to).
    """
    if lat is None and lon is None and gps_reader is not None:
        fix = read_gps(gps_reader)
        if fix is not None:
            accepted = (confirm_location(fix.lat, fix.lon)
                        if confirm_location is not None
                        else (fix.lat, fix.lon))
            if accepted is not None:
                lat, lon = accepted
    form = build_form(profile, node_name, wifi_ssid, wifi_password,
                      lat=lat, lon=lon)
    if do_join:
        joined, jmsg = join_ap(PORTAL_SSID)
        if not joined:
            return (False, f"Could not join '{PORTAL_SSID}': {jmsg}")
    return submit_form(form, host=PORTAL_HOST, post=post)
