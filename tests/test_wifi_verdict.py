"""Settings ▸ Wi-Fi: hidden networks are not offered as backslashes, and
Connect ends in a verdict the operator can act on (operator, 2026-09-22)."""
from provisioning import wifi

IW = """BSS aa:bb:cc:dd:ee:01(on wlan0)
\tsignal: -50.00 dBm
\tSSID: HomeGateway_1A2B
\tRSN:\t * Version: 1
BSS aa:bb:cc:dd:ee:02(on wlan0)
\tsignal: -70.00 dBm
\tSSID: \\x00\\x00\\x00\\x00\\x00\\x00
BSS aa:bb:cc:dd:ee:03(on wlan0)
\tsignal: -60.00 dBm
\tSSID:
"""


def test_hidden_networks_are_not_offered():
    assert wifi.is_hidden_ssid("\\x00\\x00\\x00")
    assert wifi.is_hidden_ssid("\x00\x00")
    assert wifi.is_hidden_ssid("") and wifi.is_hidden_ssid("   ")
    assert not wifi.is_hidden_ssid("HomeGateway_1A2B")
    nets = wifi._parse_iw_scan(IW)
    assert [n["ssid"] for n in nets] == ["HomeGateway_1A2B"]


def test_failures_are_explained_in_the_operators_terms():
    assert wifi.explain_failure("Error: Connection activation failed: (7) Secrets were required, but not provided.")[0] == wifi.FAIL_PASSWORD
    assert wifi.explain_failure("Error: No network with SSID 'X' found.")[0] == wifi.FAIL_OUT_OF_RANGE
    kind, tail = wifi.explain_failure("Error: Timeout 90 sec expired.")
    assert kind == wifi.FAIL_OTHER and "Timeout" in tail


def test_connect_ends_in_a_verdict_and_a_way_back():
    src = open("ui/screens/wifi_screen.py").read()
    i = src.index("def _show_result(self, ok, msg):")
    body = src[i:i + 3000]
    assert "requirement_popup(" in body
    assert 'tone="success"' in body and 'tone="warning"' in body
    assert "self._on_done()" in body            # back to Settings once connected
    assert "explain_failure(" in body
    app = open("ui/app.py").read()
    assert "WifiScreen(" in app and "on_done=" in app[app.index("WifiScreen("):app.index("WifiScreen(") + 200]
