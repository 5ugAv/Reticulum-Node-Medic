"""Clean power-off action."""

from provisioning.power import power_off


def test_power_off_success_message():
    ok, msg = power_off(run=lambda a: (0, ""))
    assert ok and "unplug" in msg.lower()


def test_power_off_failure_surfaces_reason():
    ok, msg = power_off(run=lambda a: (1, "Failed: Interactive authentication required"))
    assert not ok and "authentication" in msg.lower()


def test_power_off_uses_clean_systemctl_shutdown():
    seen = {}
    def run(argv):
        seen["argv"] = argv
        return (0, "")
    power_off(run=run)
    assert seen["argv"] == ["sudo", "-n", "systemctl", "poweroff"]
