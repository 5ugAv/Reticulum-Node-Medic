"""Before blaming a cable or a board, the medic checks its OWN power (Node
Medic 2 died of a flat battery while telling the keeper to check cables,
2026-10-06)."""
from workflows.own_supply import supply_warning, with_supply_note


def _run_with(throttled="throttled=0x0", ext5v="EXT5V_V volt(24)=5.02V"):
    def run(cmd):
        if "get_throttled" in cmd:
            return 0, throttled, ""
        if "EXT5V_V" in cmd:
            return 0, ext5v, ""
        return 1, "", ""
    return run


def test_a_healthy_medic_adds_nothing():
    assert supply_warning(_run_with()) == ""
    assert with_supply_note("Check the cable.", _run_with()) == "Check the cable."


def test_under_voltage_now_is_said_first():
    note = supply_warning(_run_with(throttled="throttled=0x50001"))
    assert "low right now" in note and "mains" in note
    msg = with_supply_note("Check the cable.", _run_with(throttled="throttled=0x1"))
    assert msg.startswith("This medic's own power is low") and msg.endswith("Check the cable.")


def test_a_sagging_5v_rail_is_named_with_its_reading():
    note = supply_warning(_run_with(ext5v="EXT5V_V volt(24)=4.61000000V"))
    assert "4.61 V" in note


def test_a_past_dip_is_a_softer_note():
    assert "dipped a short while ago" in supply_warning(_run_with(throttled="throttled=0x50000"))


def test_no_vcgencmd_means_silence():
    assert supply_warning(lambda cmd: (127, "", "not found")) == ""
