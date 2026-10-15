"""The front page's live Wi-Fi icon (keeper, 2026-10-15): grey when the radio
is off, phone-style bars by strength when joined. The numbers live in
ui.wifi_level, shared by the widget and the offline preview."""
from ui import wifi_level as wl
from provisioning import wifi


def test_bars_follow_the_phone_thresholds():
    assert wl.bars_for(True, True, 100) == 4
    assert wl.bars_for(True, True, 75) == 4
    assert wl.bars_for(True, True, 60) == 3
    assert wl.bars_for(True, True, 40) == 2
    assert wl.bars_for(True, True, 10) == 1
    assert wl.bars_for(True, True, 0) == 0
    assert wl.bars_for(True, False, 90) == 0        # not joined: nothing lit
    assert wl.bars_for(False, True, 90) == 0        # radio off: nothing lit


def test_state_is_off_searching_or_connected():
    assert wl.state_for(False, False) == wl.OFF
    assert wl.state_for(False, True) == wl.OFF      # off wins
    assert wl.state_for(True, False) == wl.SEARCHING
    assert wl.state_for(True, True) == wl.CONNECTED


def test_four_rising_bars_fit_the_box():
    bars = wl.bar_geometry(100.0, 60.0)
    assert len(bars) == 4
    assert all(0 <= x and x + w <= 100.001 for x, _y, w, _h in bars)
    heights = [h for _x, _y, _w, h in bars]
    assert heights == sorted(heights) and heights[-1] <= 60.0


def _run(table):
    def run(argv):
        key = " ".join(argv)
        for k, v in table.items():
            if k in key:
                return v
        return (1, "")
    return run


def test_signal_percent_reads_the_network_in_use():
    run = _run({"IN-USE,SIGNAL,SSID": (0, " :45:Other\n*:72:Home\n :20:Cafe\n")})
    assert wifi.signal_percent(run) == 72
    assert wifi.signal_percent(_run({"IN-USE,SIGNAL,SSID": (0, " :45:Other\n")})) is None
    assert wifi.signal_percent(_run({})) is None


def test_wifi_summary_combines_radio_connection_and_signal():
    run = _run({"radio wifi": (0, "enabled\n"),
                "GENERAL.CONNECTION,IP4.ADDRESS": (0, "GENERAL.CONNECTION:Home\nIP4.ADDRESS[1]:192.168.1.5/24\n"),
                "IN-USE,SIGNAL,SSID": (0, "*:88:Home\n")})
    assert wifi.wifi_summary(run) == {"enabled": True, "connected": True, "ssid": "Home", "signal": 88}
    off = _run({"radio wifi": (0, "disabled\n")})
    assert wifi.wifi_summary(off) == {"enabled": False, "connected": False, "ssid": "", "signal": None}


def test_the_front_page_places_the_icon_over_the_painted_symbol_and_polls():
    src = open("ui/screens/home_screen.py", encoding="utf-8").read()
    assert "WifiIcon()" in src and "_place_wifi_icon" in src and "_poll_wifi" in src
    assert "WIFI_SYMBOL = (0.5000 - 0.0375, 0.2875 - 0.0211" in src   # measured ring
    assert "schedule_interval(self._poll_wifi, 15)" in src
    assert "threading.Thread(target=work, daemon=True)" in src          # never on the UI thread
