"""Field WiFi via nmcli — connect the medic to a hotspot / venue AP. Parsing is
tested against captured nmcli output; no hardware."""

import os
import re

from provisioning import wifi


def test_scan_merges_dupes_sorts_active_then_signal():
    out = ("*:88:WPA2:HomeWiFi\n"
           ":72:WPA2:Neighbour\n"
           ":45::OpenCafe\n"
           ":90:WPA2:HomeWiFi\n")          # a stronger 2nd HomeWiFi beacon
    nets = wifi.scan_networks(run=lambda a: (0, out))
    assert [n["ssid"] for n in nets] == ["HomeWiFi", "Neighbour", "OpenCafe"]
    assert nets[0]["active"] is True and nets[0]["signal"] == 90   # merged
    assert nets[2]["secure"] is False                              # open cafe AP


# A captured ``iw dev wlan0 scan`` (indentation-agnostic — the parser strips lines;
# only ``BSS`` lines sit at column 0). Four BSS blocks: two SSIDs, one repeated at a
# stronger signal (dedup), one open cafe, one hidden (empty SSID → dropped).
IW_SCAN = """BSS aa:bb:cc:dd:ee:01(on wlan0)
    freq: 5180
    signal: -45.00 dBm
    SSID: HomeNet-5g
    RSN:     * Version: 1
BSS aa:bb:cc:dd:ee:02(on wlan0)
    freq: 2412
    signal: -60.00 dBm
    SSID: HomeNet
    RSN:     * Version: 1
BSS aa:bb:cc:dd:ee:03(on wlan0)
    freq: 2437
    signal: -75.00 dBm
    SSID: CoffeeShop
    Supported rates: 1.0 2.0 5.5 11.0
BSS aa:bb:cc:dd:ee:04(on wlan0)
    freq: 2462
    signal: -80.00 dBm
    SSID:
BSS aa:bb:cc:dd:ee:05(on wlan0)
    freq: 2412
    signal: -50.00 dBm
    SSID: HomeNet
    RSN:     * Version: 1
"""


def _iw_run(scan_out=IW_SCAN, link_ssid="", iface="wlan0"):
    """A fake nmcli/iw runner that dispatches on argv (no hardware)."""
    def run(a):
        if "scan" in a:                                  # sudo -n iw dev <if> scan
            return (0, scan_out)
        if a[:2] == ["iw", "dev"] and "link" in a:       # iw dev <if> link
            return (0, f"Connected\n\tSSID: {link_ssid}\n" if link_ssid else "Not connected.")
        if "status" in a:                                # nmcli device status
            return (0, f"{iface}:wifi:connected\nlo:loopback:unmanaged\n")
        return (1, "")
    return run


def test_scan_lists_all_nearby_from_driver():
    # The real bug: nmcli wedges to the connected AP; parse the driver's full list.
    nets = wifi.scan_networks(run=_iw_run(link_ssid="HomeNet-5g"))
    ssids = [n["ssid"] for n in nets]
    assert ssids == ["HomeNet-5g", "HomeNet", "CoffeeShop"]  # hidden dropped
    by = {n["ssid"]: n for n in nets}
    assert by["HomeNet-5g"]["active"] is True and nets[0]["active"] is True  # pinned
    assert by["HomeNet"]["signal"] == 100 and by["HomeNet"]["secure"]  # -50 dBm, dedup
    assert by["CoffeeShop"]["secure"] is False                                   # open AP
    assert by["HomeNet-5g"]["signal"] == 100                             # -45 dBm clamped


def test_scan_flags_connected_ssid():
    nets = wifi.scan_networks(run=_iw_run(link_ssid="CoffeeShop"))
    active = [n["ssid"] for n in nets if n["active"]]
    assert active == ["CoffeeShop"] and nets[0]["ssid"] == "CoffeeShop"


def test_scan_falls_back_to_nmcli_when_iw_empty():
    # No sudo / not a Pi: iw scan fails → nmcli path, still lists every AP.
    nmcli_out = ("*:88:WPA2:HomeWiFi\n:72:WPA2:Neighbour\n:45::OpenCafe\n")
    seen = {}
    def run(a):
        if "scan" in a:
            return (1, "sudo: a password is required")     # iw unavailable
        seen["argv"] = a
        return (0, nmcli_out)
    nets = wifi.scan_networks(run=run)
    assert [n["ssid"] for n in nets] == ["HomeWiFi", "Neighbour", "OpenCafe"]
    assert "--rescan" in seen["argv"] and seen["argv"][seen["argv"].index("--rescan") + 1] == "auto"


def test_scan_handles_colon_in_ssid():
    nets = wifi.scan_networks(run=lambda a: (0, ":66:WPA2:My\\:Phone\n"))
    assert nets[0]["ssid"] == "My:Phone" and nets[0]["secure"] is True


def test_connect_success_failure_and_empty():
    ok, msg = wifi.connect("HomeWiFi", "pw",
                           run=lambda a: (0, "Device 'wlan0' successfully activated"))
    assert ok and "HomeWiFi" in msg
    ok, msg = wifi.connect("HomeWiFi", "bad",
                           run=lambda a: (4, "Error: Secrets were required but not provided"))
    assert not ok and "Secrets" in msg
    ok, _ = wifi.connect("", run=lambda a: (0, ""))
    assert not ok


def test_connect_passes_password_only_when_given():
    calls = {}
    def rec(key):
        def run(a):
            calls.setdefault(key, []).append(a)
            return (0, "successfully activated")
        return run
    wifi.connect("Open", run=rec("a"))
    assert "password" not in calls["a"][0]           # first call = the connect
    wifi.connect("Sec", "pw", run=rec("b"))
    assert "password" in calls["b"][0] and "pw" in calls["b"][0]


def test_connect_sets_autoconnect_via_sudo():
    calls = []
    def run(a):
        calls.append(a)
        return (0, "successfully activated")
    wifi.connect("Home", "pw", autoconnect=True, run=run)
    modify = next(a for a in calls if "modify" in a)
    assert a_has(modify, "connection.autoconnect", "yes") and modify[:2] == ["sudo", "-n"]
    calls.clear()
    ok, msg = wifi.connect("OneOff", "pw", autoconnect=False, run=run)
    modify = next(a for a in calls if "modify" in a)
    assert "no" in modify and "won't auto-reconnect" in msg


def test_set_autoconnect_sets_priority():
    seen = {}
    wifi.set_autoconnect("Home", True, priority=10,
                         run=lambda a: (seen.update(argv=a) or (0, "")))
    assert a_has(seen["argv"], "connection.autoconnect-priority", "10")
    assert "yes" in seen["argv"]


def a_has(argv, *needles):
    return all(n in argv for n in needles)


def test_current_connection():
    out = "GENERAL.CONNECTION:HomeWiFi\nIP4.ADDRESS[1]:192.168.1.119/24\n"
    assert wifi.current_connection(run=lambda a: (0, out)) == {
        "ssid": "HomeWiFi", "ip": "192.168.1.119"}
    assert wifi.current_connection(run=lambda a: (0, "GENERAL.CONNECTION:--\n")) is None


# -- WiFi screen row rendering + the scan-freeze regression guard -------------
# The screen widgets can't be built here (Kivy's Widget.__init__ needs a Window),
# so we test the pure row text and guard the storm pattern at the source.

def test_row_label_shows_signal_secure_open_and_connected():
    from ui.screens.wifi_screen import _row_label
    active = _row_label({"ssid": "HomeWiFi", "signal": 90, "secure": True, "active": True})
    assert active.startswith("HomeWiFi") and "90%" in active and "connected" in active
    secured = _row_label({"ssid": "Neighbour", "signal": 72, "secure": True, "active": False})
    assert "72%" in secured and "open" not in secured and "connected" not in secured
    openap = _row_label({"ssid": "Cafe", "signal": 45, "secure": False, "active": False})
    assert "(open)" in openap and "connected" not in openap


def _wifi_src():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(os.path.dirname(here), "ui", "screens", "wifi_screen.py")
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_network_rows_bind_text_size_to_a_fixed_point_not_a_drifting_width():
    """The scan-freeze: a row's ``size`` was bound to a synchronous write of
    ``text_size = (width - dp(16), height)``. text_size never equalled the row
    width, so size→text_size→size could not converge — a 100% CPU redraw storm.
    Guard that no size-bound text_size subtraction comes back."""
    src = _wifi_src()
    # No ``text_size`` set to a subtracted width (the drift that never settles).
    assert not re.search(r"text_size.*v\[0\]\s*-", src), \
        "row text_size must equal the row size (a fixed point), not a subtracted width"
    # The list must not scroll horizontally (removes the width-measurement path).
    assert "do_scroll_x=False" in src


def test_wifi_screen_is_importable_without_a_window():
    # Importing the module (not building widgets) must stay Window-free, so the
    # pure helpers and the source guard above can run in CI without a display.
    import ui.screens.wifi_screen as w
    assert hasattr(w, "_row_label")
