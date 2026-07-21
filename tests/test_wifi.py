"""Field WiFi via nmcli — connect the medic to a hotspot / venue AP. Parsing is
tested against captured nmcli output; no hardware."""

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
    seen = {}
    def rec(key):
        def run(a):
            seen[key] = a
            return (0, "successfully activated")
        return run
    wifi.connect("Open", run=rec("a"))
    assert "password" not in seen["a"]
    wifi.connect("Sec", "pw", run=rec("b"))
    assert "password" in seen["b"] and "pw" in seen["b"]


def test_current_connection():
    out = "GENERAL.CONNECTION:HomeWiFi\nIP4.ADDRESS[1]:192.168.1.119/24\n"
    assert wifi.current_connection(run=lambda a: (0, out)) == {
        "ssid": "HomeWiFi", "ip": "192.168.1.119"}
    assert wifi.current_connection(run=lambda a: (0, "GENERAL.CONNECTION:--\n")) is None
