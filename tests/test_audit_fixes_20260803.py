"""Regressions from the 2026-08-03 read-only audits.

Each of these was a real defect found by an agent sweep and verified by hand
before being fixed. They are pinned here because every one of them failed
SILENTLY — no crash, no log line, just a feature that quietly did nothing.
"""

from tests.srcutil import func_source, src


def test_the_esptool_path_has_exactly_one_source():
    """It was a literal in three modules plus a FIRMWARE_VERSION constant.

    Bump the version and the literals point at a directory rnodeconf no longer
    populates — board detection and flashing both break in the field, quietly.
    That bump is imminent (eFuse board ID needs esptool >= 4.7).
    """
    from workflows.rnode_flash import esptool_cmd
    from ui.board_detect import DEFAULT_ESPTOOL
    from workflows.robust_flash import DEFAULT_ESPTOOL as ROBUST
    from workflows.rnode_v4_rgb import ESPTOOL as RGB
    assert DEFAULT_ESPTOOL == ROBUST == RGB == esptool_cmd()
    # and a version bump must move all three together
    assert "9.99" in esptool_cmd("9.99")

    for path in ("ui/board_detect.py", "workflows/robust_flash.py",
                 "workflows/rnode_v4_rgb.py"):
        assert "rnodeconf/update/1.86" not in src(path), \
            f"{path} reintroduced a hardcoded esptool path"


def test_the_sudoers_template_grants_what_the_imaging_code_runs():
    """Both entries existed by hand on the live medic and not in the repo — so
    a CLONE got a policy that images a card and then cannot configure it, and a
    Pi that never becomes a card reader."""
    policy = src("provisioning/sudoers.d/nodemedic")
    assert "prepare-card" in policy, "flash() would be refused on a fresh medic"
    assert "rpiboot" in policy, "the Pi-as-card-reader path would be refused"
    assert "NM_PIBOOT" in policy.split("nodemedic ALL=(root)")[1], \
        "NM_PIBOOT defined but never granted"


def test_a_fresh_build_lap_forgets_the_last_card():
    """_from_imaging was set and never cleared, so every later visit to BIRTH
    claimed a card had just been written for the PREVIOUS node."""
    reset = func_source("ui/screens/birth_screen.py", "begin_guided")
    assert "_from_imaging" in reset
