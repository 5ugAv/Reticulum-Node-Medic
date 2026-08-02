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
    """Still true, just carried differently: the flag reaches the root helper
    in its config instead of as a pile of mount/tee commands."""
    import base64 as _b, json as _j
    from provisioning import pi_imager
    captured = {}

    def shell(cmd):
        if "base64 -d >" in cmd:
            blob = cmd.split("echo ")[1].split(" |")[0].strip("'")
            captured.update(_j.loads(_b.b64decode(blob).decode()))
        return (0, "")

    ok, msg = pi_imager.flash(
        "/dev/sdb", "faith", "pi", "pw", image_path="/tmp/x.img.xz",
        run=_medic_run, run_shell=shell, pw_hasher=lambda p: "$6$hash",
        authorized_keys=[])
    assert ok, msg
    assert captured.get("cable_link") is True
    assert "USB cable" in msg


def test_a_failed_cable_bake_does_not_throw_away_a_good_card():
    """The bake moved into the root helper, but its DEGRADATION had to survive
    the move: a card without the cable link still boots and joins WiFi, so
    losing the link must not discard an otherwise good card. Account
    activation, in the same helper, stays fatal — without it the Pi boots and
    refuses every login."""
    src = open("assets/scripts/prepare_card.py").read()
    boot = src[src.index("def write_boot"):src.index("def _write")]
    assert "NOT fatal" in boot
    assert "PREPARE_WARN" in boot and "over WiFi" in boot
    # and the fatal one is still fatal
    act = src[src.index("def activate_account"):]
    assert "fail(" in act, "a missing account must still abort"


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


def test_a_wifi_free_card_is_not_advertised_as_joining_wifi():
    """It has no PSK at all — telling the operator it will join WiFi sends them
    hunting for a node that can never appear on their network."""
    from provisioning import pi_imager
    ok, msg = pi_imager.flash(
        "/dev/sdb", "hope", "pi", "pw", image_path="/tmp/x.img.xz",
        run=_medic_run, run_shell=lambda c: (0, ""),
        pw_hasher=lambda p: "$6$hash", authorized_keys=[])
    assert ok
    assert "join WiFi" not in msg
    assert "No WiFi was configured" in msg and "USB cable" in msg


def test_a_wifi_card_still_says_so():
    from provisioning import pi_imager
    ok, msg = pi_imager.flash(
        "/dev/sdb", "hope", "pi", "pw", wifi_ssid="HomeNet",
        wifi_password="x", image_path="/tmp/x.img.xz",
        run=_medic_run, run_shell=lambda c: (0, ""),
        pw_hasher=lambda p: "$6$hash", authorized_keys=[])
    assert ok and "join WiFi" in msg


# --- the operator should never type an address ------------------------------

def test_the_cable_is_tried_before_any_network_route(monkeypatch):
    """Asking a field operator for the IP of a Pi that is physically plugged
    into the tool was the complaint that started this whole path."""
    from provisioning import pi_discover
    monkeypatch.setattr(pi_discover, "cable_address", lambda *a, **k: "10.55.0.1")
    called = []
    monkeypatch.setattr(pi_discover, "resolve",
                        lambda n: called.append(n) or "192.168.1.50")
    r = pi_discover.find_pi()
    assert r["address"] == "10.55.0.1"
    assert "cable" in r["how"]
    assert not called, "went to the network even though the cable was live"


def test_an_explicit_hostname_still_wins_over_the_cable(monkeypatch):
    """If the operator names a node, honour it — they may be birthing one Pi
    while another sits on the cable."""
    from provisioning import pi_discover
    monkeypatch.setattr(pi_discover, "cable_address", lambda *a, **k: "10.55.0.1")
    monkeypatch.setattr(pi_discover, "resolve", lambda n: "192.168.1.50")
    r = pi_discover.find_pi("faith")
    assert r["ip"] == "192.168.1.50"


def test_no_cable_falls_back_to_the_network_as_before(monkeypatch):
    from provisioning import pi_discover
    monkeypatch.setattr(pi_discover, "cable_address", lambda *a, **k: "")
    monkeypatch.setattr(pi_discover, "last_imaged_pi", lambda path=None: {"hostname": "hope"})
    monkeypatch.setattr(pi_discover, "resolve", lambda n: "192.168.1.77")
    r = pi_discover.find_pi()
    assert r["ip"] == "192.168.1.77"


def test_cable_lookup_never_raises(monkeypatch):
    """A discovery hiccup must not be able to block a birth."""
    from provisioning import pi_discover, link
    monkeypatch.setattr(link, "discover_peer",
                        lambda **k: (_ for _ in ()).throw(OSError("no iface")))
    assert pi_discover.cable_address() == ""


# --- never hand the build a Pi we can't tie to this build -------------------

def test_a_network_sweep_never_yields_an_address_to_auto_fill(monkeypatch):
    """2026-08-02: the sweep offered 192.168.1.42 — a real but UNRELATED Pi on
    the operator's LAN — as the build target, while the Pi actually being built
    sat on USB in card-reader mode with no network address at all. Provisioning
    rewrites the target's services and config."""
    from provisioning import pi_discover
    monkeypatch.setattr(pi_discover, "cable_address", lambda *a, **k: "")
    monkeypatch.setattr(pi_discover, "last_imaged_pi", lambda path=None: {})
    monkeypatch.setattr(pi_discover, "neighbours",
                        lambda: [{"ip": "192.168.1.42", "mac": "02:00:00:0a:00:0a"}])
    r = pi_discover.find_pi()
    assert r["address"] == "", "a stranger's Pi would have been filled in"
    assert r["confirmed"] is False
    assert "can't tell if any of them is the one" in r["how"]
    assert "192.168.1.42" in r["candidates"]      # offered, not chosen


def test_the_cable_and_our_own_name_ARE_confirmed(monkeypatch):
    from provisioning import pi_discover
    monkeypatch.setattr(pi_discover, "cable_address", lambda *a, **k: "10.55.0.1")
    assert pi_discover.find_pi()["confirmed"] is True

    monkeypatch.setattr(pi_discover, "cable_address", lambda *a, **k: "")
    monkeypatch.setattr(pi_discover, "last_imaged_pi",
                        lambda path=None: {"hostname": "hope"})
    monkeypatch.setattr(pi_discover, "resolve", lambda n: "192.168.1.50")
    assert pi_discover.find_pi()["confirmed"] is True


def test_the_screen_refuses_to_autofill_an_unconfirmed_address():
    src = open("ui/screens/birth_screen.py").read()
    assert 'res or {}).get("confirmed"' in src


# --- recognising our OWN nodes on the network -------------------------------
# 2026-08-02: the sweep offered 192.168.1.42 as a build target. It was
# EVERYWHERE, the operator's live propagation node — and the medic knew its
# name all along (mDNS resolves it) and had it in the kin roster. It simply
# wasn't looking.

def test_a_known_node_is_named_and_marked_as_ours(monkeypatch):
    from provisioning import pi_discover
    monkeypatch.setattr(pi_discover, "name_for_ip", lambda ip: "everywhere")
    got = pi_discover.identify("192.168.1.42", kin={"everywhere": "EVERYWHERE"})
    assert got["kin"] == "EVERYWHERE"
    assert "already one of your nodes" in got["label"]
    assert "192.168.1.42" in got["label"]


def test_an_unknown_pi_is_named_but_not_claimed(monkeypatch):
    from provisioning import pi_discover
    monkeypatch.setattr(pi_discover, "name_for_ip", lambda ip: "someones-pi")
    got = pi_discover.identify("192.168.1.9", kin={"everywhere": "EVERYWHERE"})
    assert got["kin"] == ""
    assert "someones-pi" in got["label"]
    assert "already one of your nodes" not in got["label"]


def test_a_nameless_host_falls_back_to_its_address(monkeypatch):
    from provisioning import pi_discover
    monkeypatch.setattr(pi_discover, "name_for_ip", lambda ip: "")
    assert pi_discover.identify("192.168.1.9", kin={})["label"] == "192.168.1.9"


def test_a_sweep_finding_only_our_own_nodes_says_there_is_nothing_to_build(monkeypatch):
    """The exact situation on the bench: the one Pi on the LAN was EVERYWHERE."""
    from provisioning import pi_discover
    monkeypatch.setattr(pi_discover, "cable_address", lambda *a, **k: "")
    monkeypatch.setattr(pi_discover, "last_imaged_pi", lambda path=None: {})
    monkeypatch.setattr(pi_discover, "neighbours",
                        lambda: [{"ip": "192.168.1.42", "mac": "02:00:00:0a:00:0a"}])
    monkeypatch.setattr(pi_discover, "known_kin_names",
                        lambda *a, **k: {"everywhere": "EVERYWHERE"})
    monkeypatch.setattr(pi_discover, "name_for_ip", lambda ip: "everywhere")
    r = pi_discover.find_pi()
    assert r["address"] == "" and r["confirmed"] is False
    assert "EVERYWHERE" in r["how"]
    assert "already yours" in r["how"] and "Nothing here to build" in r["how"]


def test_kin_names_are_matched_case_and_space_insensitively():
    from provisioning import pi_discover

    class _R(dict):
        pass
    import monitor.kin_roster as kr
    orig = kr.load_roster
    kr.load_roster = lambda *a, **k: {"h": {"name": "FAITH RTnode"}}
    try:
        names = pi_discover.known_kin_names()
    finally:
        kr.load_roster = orig
    assert names.get("faith rtnode") == "FAITH RTnode"
    assert names.get("faith-rtnode") == "FAITH RTnode"   # hostnames use dashes


# --- the hand-off must actually be shown ------------------------------------

def test_the_handoff_is_in_the_persistent_panel_not_only_a_popup():
    """A dismissed popup is no use once the operator's hands are full of two
    boards. The flow used to simply END after the build (operator, 2026-08-02)."""
    src = open("ui/screens/birth_screen.py").read()
    assert "_handoff_block" in src
    panel = src[src.index("def _outcome_panel"):src.index("def _handoff_block")]
    assert "_handoff_block(board)" in panel, "not wired into the outcome panel"


def test_the_handoff_names_all_three_physical_actions():
    src = open("ui/screens/birth_screen.py").read()
    block = src[src.index("def _handoff_block"):src.index("def _finish")]
    assert "Unplug BOTH" in block
    assert "DATA" in block and "PWR IN" in block     # the Zero's two identical ports
    assert "Power the" in block


def test_the_completion_popup_does_not_claim_it_is_finished():
    """The node does not exist yet — both boards are still on the medic."""
    src = open("ui/screens/birth_screen.py").read()
    assert "Built — but not finished yet" in src
    assert "One last step" in src


def test_the_handoff_reuses_the_shared_wording_not_a_copy():
    """Screen and model must not drift: the radio-identity warning comes from
    cable_birth, which knows whether the serial actually pins one board."""
    src = open("ui/screens/birth_screen.py").read()
    block = src[src.index("def _handoff_block"):src.index("def _finish")]
    assert "unmoved_warning" in block and "stable_port_for" in block


def test_the_handoff_is_honest_about_power_after_the_medic_lets_go():
    """During the build the MEDIC powers the radio. Afterwards the Pi does, and
    a Zero+V3 pairing is exactly the case the power model warns about."""
    src = open("ui/screens/birth_screen.py").read()
    block = src[src.index("def _handoff_block"):src.index("def _finish")]
    assert "power_compat" in block
    assert "was powering" in block


# --- don't ask an unimaged Pi for an address --------------------------------
# 2026-08-02: the operator reached "OK — start" with a Pi in boot-ROM mode and
# got "Pi address needed". The Pi had a BLANK CARD — no OS, no network, and no
# way to have an address. A true statement that cannot be acted on.

SRC = open("ui/screens/birth_screen.py").read()


def test_the_screen_checks_whether_the_pi_has_an_os_before_asking_for_an_address():
    assert "_pi_needs_imaging" in SRC
    i_check = SRC.index("if self._pi_needs_imaging():")
    i_cable = SRC.index('cable = ""')
    assert i_check < i_cable, "must decide BEFORE falling into the address flow"


def test_an_unimaged_pi_is_routed_to_imaging_not_to_a_text_box():
    # Bound the block by the NEXT branch rather than a character count: the
    # power-compatibility banner and the blocked-pairing options now sit between
    # the branch and the route, and a fixed window kept going stale.
    _start = SRC.index("if self._pi_needs_imaging():")
    block = SRC[_start:SRC.index("cable = \"\"", _start)]
    assert "no operating system yet" in block
    assert "no address to enter" in block
    assert "_go_image_pi" in block


def test_the_check_fails_safe_when_it_cannot_tell():
    """Wrongly calling a WORKING Pi blank would send the operator to reimage a
    node that was fine — far worse than falling through to the address flow."""
    block = SRC[SRC.index("def _pi_needs_imaging"):SRC.index("def _go_image_pi")]
    assert "return False" in block
    assert "except Exception" in block


def test_only_pre_os_states_count_as_needing_imaging():
    """A GADGET Pi has an OS and is reachable — it must NOT be offered imaging."""
    block = SRC[SRC.index("def _pi_needs_imaging"):SRC.index("def _go_image_pi")]
    assert "BOOTROM" in block and "CARD_READER" in block
    assert "GADGET" not in block
