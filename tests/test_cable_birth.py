"""Cable birth — Pi and radio both plugged into the medic, no WiFi, no hub.

Pure plan/command building, so all of this runs with no hardware and no Kivy.
"""

import base64

import pytest

from node_profile import NodeHardware
from provisioning import cable_birth as cb
from provisioning.gadget import (GADGET_USB_IP, GADGET_SERVICE_PATH,
                                 cmdline_with_gadget, config_txt_with_gadget)


# --- which boards can do it ------------------------------------------------

def test_zero_and_3aplus_can_cable_birth_even_though_they_deploy_on_uart():
    """The whole point: reachability.py gives a Zero UART because the radio owns
    its USB port once DEPLOYED. During birth the radio is on the medic, so the
    port is free and the cable link is available."""
    assert cb.supports_cable_birth(NodeHardware.PI_ZERO_2W)
    assert cb.supports_cable_birth(NodeHardware.PI_3A_PLUS)
    from provisioning.reachability import link_kind
    assert link_kind(NodeHardware.PI_ZERO_2W) == "uart"    # deployed life: unchanged


def test_a_radio_board_is_not_a_cable_birth_target():
    assert not cb.supports_cable_birth(NodeHardware.HELTEC_V3)
    assert not cb.supports_cable_birth(NodeHardware.UNKNOWN)


# --- baking the link into a cold card --------------------------------------

def _decoded_script(cmds):
    """Pull the base64'd python out of the boot commands so we can run it."""
    for c in cmds:
        if "base64 -d | sudo python3" in c:
            blob = c.split("echo ")[1].split(" |")[0].strip("'")
            return base64.b64decode(blob).decode()
    raise AssertionError("no baking script found")


def test_boot_bake_checks_it_is_really_a_pi_boot_partition_first():
    cmds = cb.boot_partition_commands("/mnt/x")
    assert cmds[0].startswith("test -f /mnt/x/config.txt")
    assert "cmdline.txt" in cmds[0]


def test_boot_bake_reuses_the_tested_transforms_rather_than_copying_rules():
    """If the baking script reimplemented the cmdline rules they could drift
    from provisioning.gadget. It must import them."""
    script = _decoded_script(cb.boot_partition_commands("/mnt/x"))
    assert "from provisioning.gadget import" in script
    assert "cmdline_with_gadget" in script and "config_txt_with_gadget" in script
    # and it must not hardcode the tokens itself
    assert "dwc2,g_ether" not in script


def test_the_baking_script_actually_works_on_real_boot_files(tmp_path):
    """Run the generated script for real against a fake boot partition."""
    import subprocess, sys
    (tmp_path / "cmdline.txt").write_text(
        "console=serial0,115200 root=PARTUUID=abc rootfstype=ext4 "
        "fsck.repair=yes rootwait quiet\n")
    (tmp_path / "config.txt").write_text("dtparam=audio=on\n")
    script = _decoded_script(cb.boot_partition_commands(str(tmp_path)))
    p = subprocess.run([sys.executable, "-", cb.repo_root(), str(tmp_path)],
                       input=script, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    cmdline = (tmp_path / "cmdline.txt").read_text()
    config = (tmp_path / "config.txt").read_text()
    assert "modules-load=dwc2,g_ether" in cmdline
    assert "dtoverlay=dwc2" in config
    # dwc2 must precede the rootfs handoff to enumerate in time
    assert cmdline.index("rootwait") < cmdline.index("modules-load=")
    assert cmdline.count("\n") == 1, "cmdline.txt must stay a single line"


def test_baking_twice_changes_nothing(tmp_path):
    import subprocess, sys
    (tmp_path / "cmdline.txt").write_text("root=/dev/x rootwait\n")
    (tmp_path / "config.txt").write_text("dtparam=audio=on\n")
    script = _decoded_script(cb.boot_partition_commands(str(tmp_path)))
    for _ in range(2):
        subprocess.run([sys.executable, "-", cb.repo_root(), str(tmp_path)],
                       input=script, capture_output=True, text=True)
    assert (tmp_path / "cmdline.txt").read_text().count("dwc2") == 1
    assert (tmp_path / "config.txt").read_text().count("dtoverlay=dwc2") == 1


def test_rootfs_gets_the_static_ip_unit_AND_is_enabled_without_systemctl():
    """A cold rootfs has no running systemd, so `systemctl enable` is impossible
    — the wants-symlink must be created directly or the unit never starts."""
    cmds = cb.rootfs_commands("/mnt/root")
    joined = " ".join(cmds)
    assert f"/mnt/root{GADGET_SERVICE_PATH}" in joined
    assert "multi-user.target.wants" in joined
    assert "ln -sf" in joined


def test_the_unit_content_survives_transport_unmangled():
    """The unit body contains $(seq ...) and quotes — a here-doc would mangle
    them, so it goes over base64."""
    cmds = cb.rootfs_commands("/mnt/root")
    blob = [c for c in cmds if "base64 -d" in c][0]
    payload = blob.split("echo ")[1].split(" |")[0].strip("'")
    decoded = base64.b64decode(payload).decode()
    assert "ExecStart=/sbin/ip addr add" in decoded
    assert GADGET_USB_IP in decoded
    assert "$(seq 1 20)" in decoded


# --- the whole-card plan ---------------------------------------------------

@pytest.mark.parametrize("dev,boot,root", [
    ("/dev/sdb", "/dev/sdb1", "/dev/sdb2"),
    ("/dev/mmcblk0", "/dev/mmcblk0p1", "/dev/mmcblk0p2"),
])
def test_partition_naming_for_both_device_styles(dev, boot, root):
    joined = " ".join(cb.bake_commands(dev))
    assert f"mount {boot} " in joined
    assert f"mount {root} " in joined


def test_every_mount_is_unmounted_again():
    cmds = cb.bake_commands("/dev/sdb")
    joined = " ".join(cmds)
    assert joined.count("umount") == 2, "both partitions must be released"
    assert cmds[-1].startswith("sudo sync")


def test_bake_touches_only_the_named_device():
    for cmd in cb.bake_commands("/dev/sdb"):
        assert "/dev/sda" not in cmd and "/dev/mmcblk0" not in cmd


# --- the operator walkthrough ----------------------------------------------

def test_plan_alternates_the_operator_and_the_medic_and_ends_joined():
    p = cb.plan("Pi Zero 2 W", "Heltec LoRa32 V3")
    assert p.steps[0]["who"] == "operator"
    assert "DATA USB port" in p.steps[0]["body"]
    assert p.steps[-1]["who"] == "operator"
    assert "Unplug both" in p.steps[-1]["title"]
    assert {s["who"] for s in p.steps} == {"operator", "medic"}


def test_plan_flashes_the_radio_on_the_medic_not_on_the_pi():
    p = cb.plan("Pi Zero 2 W", "Heltec LoRa32 V3")
    flash = [s for s in p.steps if "Flash" in s["title"]][0]
    assert flash["who"] == "medic"
    assert p.titles.index(flash["title"]) < p.titles.index(
        [t for t in p.titles if t.startswith("Unplug")][0])


def test_plan_carries_the_radios_stable_port_so_it_survives_the_move():
    by_id = "/dev/serial/by-id/usb-Silicon_Labs_CP2102N_1234-if00-port0"
    p = cb.plan("Pi Zero 2 W", "Heltec LoRa32 V3", radio_by_id=by_id)
    assert any(by_id in s["body"] for s in p.steps)


def test_power_note_is_honest_that_the_field_limit_still_applies():
    note = cb.power_note("Pi Zero 2 W", "Heltec LoRa32 V3")
    assert "doesn't arise during the build" in note
    assert "still apply in the field" in note


# --- the imager bakes it in ------------------------------------------------

def _medic_run(argv, **kw):
    """A medic whose system disk is mmcblk0 with a USB SD reader at sdb —
    mirrors tests/test_pi_imager.py so the real safety guard is exercised."""
    if argv[:2] == ["findmnt", "-no"]:
        return (0, "/dev/mmcblk0p2")
    if argv[:2] == ["lsblk", "-no"] and "PKNAME" in argv:
        return (0, "mmcblk0")
    if argv[:2] == ["lsblk", "-dno"]:
        return (0, "mmcblk0 59.5G disk mmc  0 \nzram0 2G disk   0 "
                   "\nsdb 29.7G disk usb  1 Generic SD Reader")
    return (0, "")


def test_imager_bakes_the_cable_link_into_the_card_it_writes():
    from provisioning import pi_imager
    seen = []

    def fake_shell(cmd):
        seen.append(cmd)
        return (0, "")

    ok, msg = pi_imager.flash(
        "/dev/sdb", "faith", "pi", "pw", image_path="/tmp/x.img.xz",
        run=_medic_run, run_shell=fake_shell,
        pw_hasher=lambda p: "$6$hash", authorized_keys=[])
    assert ok, msg
    joined = " ".join(seen)
    assert "multi-user.target.wants" in joined, "gadget unit never installed"
    assert "base64 -d | sudo python3" in joined, "boot files never transformed"
    assert "USB cable" in msg


def test_a_failed_bake_does_not_fail_the_whole_card():
    """A card that boots and joins WiFi is still usable — losing the cable link
    must degrade, not abort."""
    from provisioning import pi_imager
    calls = {"n": 0}

    def fake_shell(cmd):
        calls["n"] += 1
        # fail only the gadget-unit write, after the image + config succeeded
        if "multi-user.target.wants" in cmd:
            return (1, "mount is read-only")
        return (0, "")

    ok, msg = pi_imager.flash(
        "/dev/sdb", "faith", "pi", "pw", image_path="/tmp/x.img.xz",
        run=_medic_run, run_shell=fake_shell,
        pw_hasher=lambda p: "$6$hash", authorized_keys=[])
    assert ok is True
    assert "could not be baked" in msg and "over WiFi" in msg


def test_cable_link_can_be_turned_off():
    from provisioning import pi_imager
    seen = []
    pi_imager.flash("/dev/sdb", "faith", "pi", "pw", image_path="/tmp/x.img.xz",
                    run=_medic_run,
                    run_shell=lambda c: (seen.append(c), (0, ""))[1],
                    pw_hasher=lambda p: "$6$hash", authorized_keys=[],
                    cable_link=False)
    assert "multi-user.target.wants" not in " ".join(seen)


# --- carrying the radio's identity across the move -------------------------

_LS_BY_ID = (0,
    "total 0\n"
    "lrwxrwxrwx 1 root root 13 Aug  1 19:00 "
    "usb-Silicon_Labs_CP2102N_abc123-if00-port0 -> ../../ttyUSB0\n"
    "lrwxrwxrwx 1 root root 13 Aug  1 19:00 "
    "usb-Espressif_USB_JTAG_serial_debug_unit_9f8-if00 -> ../../ttyACM0\n")


def test_stable_port_is_the_by_id_name_so_it_survives_the_move():
    """The whole hand-off depends on this: the name must be derived from the
    chip, not from enumeration order, or the Pi won't find the radio."""
    got = cb.stable_port_for("/dev/ttyUSB0", runner=lambda a: _LS_BY_ID)
    assert got == ("/dev/serial/by-id/"
                   "usb-Silicon_Labs_CP2102N_abc123-if00-port0")


def test_stable_port_picks_the_right_one_when_several_radios_are_attached():
    """Jonesey (the medic's own radio) is always plugged in — resolving the
    wrong link would point the Pi at the medic's board."""
    got = cb.stable_port_for("/dev/ttyACM0", runner=lambda a: _LS_BY_ID)
    assert "Espressif" in got and "CP2102" not in got


def test_stable_port_falls_back_rather_than_inventing_a_path():
    assert cb.stable_port_for("/dev/ttyUSB9",
                              runner=lambda a: _LS_BY_ID) == "/dev/ttyUSB9"
    assert cb.stable_port_for("/dev/ttyUSB0",
                              runner=lambda a: (1, "")) == "/dev/ttyUSB0"


def test_warning_distinguishes_a_pinned_radio_from_an_anonymous_one():
    pinned = cb.unmoved_warning("/dev/serial/by-id/usb-Silicon_Labs_x-if00-port0")
    anon = cb.unmoved_warning("/dev/ttyUSB0")
    assert "this exact radio" in pinned
    assert "no unique serial" in anon and "only the radio you just" in anon


# --- a by-id path is only as unique as its serial ---------------------------

_LIVE_V3 = ("/dev/serial/by-id/"
            "usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0")
_LIVE_JONESEY = ("/dev/serial/by-id/"
                 "usb-Espressif_USB_JTAG_serial_debug_unit_A1:B2:C3:D4:E5:F6-if00")


def test_serial_is_extracted_from_real_by_id_names():
    """Both observed live on the medic, 2026-08-01."""
    assert cb.by_id_serial(_LIVE_V3) == "0001"
    assert cb.by_id_serial(_LIVE_JONESEY) == "A1:B2:C3:D4:E5:F6"
    assert cb.by_id_serial("/dev/ttyUSB0") == ""


def test_the_heltec_v3s_factory_serial_is_not_treated_as_an_identity():
    """Live finding: the V3's CP2102 reports 0001, the factory default — every
    board off that line has it. Claiming it pins one radio would be false."""
    assert not cb.is_uniquely_identified(_LIVE_V3)
    assert cb.is_uniquely_identified(_LIVE_JONESEY)


def test_warning_only_promises_a_pinned_radio_when_it_can_keep_it():
    assert "this exact radio" in cb.unmoved_warning(_LIVE_JONESEY)
    v3 = cb.unmoved_warning(_LIVE_V3)
    assert "this exact radio" not in v3
    assert "default serial number" in v3 and "only the radio you just" in v3


def test_a_bare_device_path_is_still_handled():
    assert "first radio it finds" in cb.unmoved_warning("/dev/ttyUSB0")
