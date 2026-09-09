"""A certificate proves a board was flashed, not what it was flashed AS.

Operator, 2026-09-09, building a Pi Zero 2 W + RAK4631. The RAK had been
birthed earlier as an RTNode-2400 and still held its certificate, so the Pi
path's shortcut declared it a ready RNode, skipped the flash and jumped a step
("it asked me to unplug it very quickly"). The Pi then got a standalone mesh
node where it needed a modem and came up `lora_online: false`. The board was
announcing the truth the whole time, in its USB product string:

    239a:8029 Adafruit RAK4631 RTNode-2400

The shortcut exists for a good reason — it stopped five needless reflashes in
one night (2026-08-14). It just asked about provenance where it needed to ask
about role.
"""

from tests.srcutil import func_source

SCREEN = "ui/screens/birth_guide_screen.py"


def test_the_shortcut_requires_rnode_firmware_not_just_a_certificate():
    body = func_source(SCREEN, "_render_step", cls="BirthGuideScreen")
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    assert "_is_rnode_firmware" in code, \
        "a cert alone must not satisfy the ready-radio shortcut"
    i = code.index("cert_for_usb_serial")
    after = code[i:]
    assert "_is_rnode_firmware" in after.split("_radio_verified = True")[0], \
        "the firmware check must gate the skip, not follow it"


def test_a_wrong_role_board_is_flashed_rather_than_trusted():
    body = func_source(SCREEN, "_render_step", cls="BirthGuideScreen")
    assert "elif cert:" in body, \
        "cert-but-wrong-role needs its own branch, so it is flashed"
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    branch = code.split("elif cert:", 1)[1].split("\n\n")[0]
    assert "_radio_verified = True" not in branch, \
        "a wrong-role board must NOT be treated as ready"


def test_the_skip_is_never_silent():
    """It advanced a step with no screen and no note, so there was nothing for
    the operator to disagree with."""
    body = func_source(SCREEN, "_render_step", cls="BirthGuideScreen")
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    assert code.count("self._trace(") >= 2, \
        "both outcomes of the shortcut must leave a line in the log"


def test_the_firmware_probe_fails_closed():
    """An unreadable identity must mean 'flash it', never 'trust it'. A
    needless reflash costs a minute; the other way cost a whole build."""
    probe = func_source(SCREEN, "_is_rnode_firmware")
    assert "return False" in probe, "unreadable identity -> flash it"
    code = "\n".join(l for l in probe.splitlines()
                     if not l.strip().startswith("#"))
    assert "rtnode" in code.lower(), "recognise an RTNode-2400 by its USB name"
