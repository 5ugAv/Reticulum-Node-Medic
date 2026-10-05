"""Settings ▸ Wi-Fi has an on/off switch for the medic's Wi-Fi radio (keeper,
2026-10-06: "there should be a switch under Wi-Fi that says turn Wi-Fi off"),
first wanted for a Wi-Fi-off clone test. NetworkManager's polkit refuses the
login user, so it runs through one exact sudo rule — no wildcard."""
from provisioning import wifi
from tests.srcutil import src


def test_reading_the_radio_needs_no_sudo_and_unknown_reads_as_on():
    seen = []

    def run(argv):
        seen.append(argv)
        return 0, "disabled\n"
    assert wifi.radio_enabled(run=run) is False
    assert seen == [["nmcli", "radio", "wifi"]]
    assert wifi.radio_enabled(run=lambda a: (0, "enabled\n")) is True
    assert wifi.radio_enabled(run=lambda a: (127, "nmcli: not found")) is True


def test_switching_uses_the_exact_sudo_argv():
    seen = []

    def run(argv):
        seen.append(argv)
        return 0, ""
    assert wifi.set_radio(False, run=run) == (True, "")
    assert wifi.set_radio(True, run=run) == (True, "")
    assert seen == [["sudo", "-n", "nmcli", "radio", "wifi", "off"],
                    ["sudo", "-n", "nmcli", "radio", "wifi", "on"]]


def test_a_missing_sudo_rule_is_named_as_a_permission_problem():
    ok, why = wifi.set_radio(False, run=lambda a: (1, "sudo: a password is required"))
    assert not ok and why == "permission"


def test_the_sudo_rule_is_exact_and_granted():
    s = src("provisioning/sudoers.d/nodemedic")
    assert "/usr/bin/nmcli radio wifi off, \\\n    /usr/bin/nmcli radio wifi on\n" in s
    assert "nmcli radio wifi *" not in s and "nmcli radio *" not in s
    grant = s[s.index("nodemedic ALL=(root) NOPASSWD:"):]
    assert "NM_RADIO" in grant.split("\n\n")[0]


def test_the_screen_has_the_switch_and_does_not_scan_while_off():
    w = src("ui/screens/wifi_screen.py")
    assert "self.radio = Switch(" in w and "wifi.set_radio(on" in w
    assert "if self._busy or not self.radio.active:" in w
    assert "Wi-Fi is off. This medic will not join any" in w


def test_the_status_line_cannot_loop_the_layout():
    """The first deploy froze the medic: the status label's height followed its
    texture while _line tied text_size to the whole size — a redraw loop
    ("too much iteration done before the next frame"). It must use
    grow_to_text, which wraps on width only."""
    w = src("ui/screens/wifi_screen.py")
    assert "self.status = grow_to_text(" in w
    assert "bind(texture_size=" not in w
