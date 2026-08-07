"""On-medic Pi SD imaging — SAFETY (never the system disk) + config generation."""

from provisioning import pi_imager as pi


# A fake `run(argv) -> (code, out)` that mimics the medic: system disk mmcblk0,
# plus a USB card reader at sdb.
def _medic_run(with_reader=True):
    lsblk_disks = "mmcblk0 59.5G disk mmc  0 \nzram0 2G disk   0 "
    if with_reader:
        lsblk_disks += "\nsdb 29.7G disk usb  1 Generic SD Reader"

    def run(argv):
        if argv[:3] == ["findmnt", "-no", "SOURCE"]:
            return (0, "/dev/mmcblk0p2\n")
        if argv[:2] == ["lsblk", "-no"] and "PKNAME" in argv:
            return (0, "mmcblk0\n")
        if argv[:2] == ["lsblk", "-dno"]:
            return (0, lsblk_disks)
        if argv[:2] == ["openssl", "passwd"]:
            return (0, "$6$abc$deadbeefhash\n")
        return (0, "")
    return run


def test_system_disk_is_the_root_device():
    assert pi.system_disk(run=_medic_run()) == "mmcblk0"


def test_target_disks_are_removable_usb_only_never_the_system_disk():
    targets = pi.list_target_disks(run=_medic_run(with_reader=True))
    names = {t["name"] for t in targets}
    assert names == {"sdb"}                      # the USB reader
    assert "mmcblk0" not in names                # NEVER the medic's own disk
    assert "zram0" not in names and not any(n.startswith("loop") for n in names)


def test_no_targets_when_no_reader():
    assert pi.list_target_disks(run=_medic_run(with_reader=False)) == []


def test_is_safe_target_refuses_system_disk_and_accepts_reader():
    run = _medic_run(with_reader=True)
    assert pi.is_safe_target("/dev/sdb", run=run) is True
    assert pi.is_safe_target("/dev/mmcblk0", run=run) is False   # system disk
    assert pi.is_safe_target("/dev/sdz", run=run) is False       # not present


def test_flash_REFUSES_the_system_disk_and_never_writes():
    shell_calls = []
    ok, msg = pi.flash("/dev/mmcblk0", "myhost", "pi", "pw",
                       run=_medic_run(), run_shell=lambda c: shell_calls.append(c) or (0, ""))
    assert ok is False and "system disk" in msg.lower() or "removable" in msg.lower()
    assert shell_calls == []                     # CRITICAL: nothing was written


def test_flash_refuses_absent_device():
    ok, msg = pi.flash("/dev/sdz", "h", "pi", "pw", run=_medic_run(),
                       run_shell=lambda c: (0, ""))
    assert not ok


def test_flash_happy_path_writes_then_configures():
    shell = []
    ok, msg = pi.flash("/dev/sdb", "faithpi", "pi", "secret",
                       wifi_ssid="Home", wifi_password="wpw", image_path="/img.xz",
                       run=_medic_run(), run_shell=lambda c: shell.append(c) or (0, ""))
    assert ok, msg
    joined = "\n".join(shell)
    assert "dd of=/dev/sdb" in joined and "/img.xz" in joined      # image written
    # Configuration is now ONE root operation (the prepare-card helper) instead
    # of a pile of mount/tee calls — the mounting happens inside it, as root,
    # behind a single narrow sudoers entry.
    assert pi.PREPARE_CARD in joined
    assert "--device /dev/sdb" in joined
    import base64 as _b, json as _j
    blob = [c for c in shell if "base64 -d >" in c][0]
    cfg = _j.loads(_b.b64decode(blob.split("echo ")[1].split(" |")[0].strip("'")).decode())
    assert 'hostname = "faithpi"' in cfg["custom_toml"]             # config applied


def test_build_custom_toml_has_hostname_hashed_pw_ssh_and_wifi():
    toml = pi.build_custom_toml("faithpi", "pi", "secret", wifi_ssid="Home",
                                wifi_password="wpw", wifi_country="AU",
                                pw_hasher=lambda p: "$6$HASH")
    assert 'hostname = "faithpi"' in toml
    assert 'password = "$6$HASH"' in toml and "password_encrypted = true" in toml
    assert "[ssh]" in toml and "enabled = true" in toml
    assert 'ssid = "Home"' in toml and 'country = "AU"' in toml


def test_custom_toml_omits_wifi_when_no_ssid():
    toml = pi.build_custom_toml("h", "pi", "pw", pw_hasher=lambda p: "x")
    assert "[wlan]" not in toml


def test_carried_image_found_or_none(tmp_path):
    img = tmp_path / "pi.img.xz"
    img.write_bytes(b"x")
    assert pi.carried_image([str(img)]) == str(img)
    assert pi.carried_image([str(tmp_path / "nope.xz")]) is None


def test_config_carries_the_medics_public_key():
    """The propagation birth logs in by KEY, so an imaged card must already
    trust the medic — otherwise the medic images a Pi it cannot reach
    (2026-08-01)."""
    from provisioning.pi_imager import build_custom_toml
    toml = build_custom_toml("rnm-prop-01", "pi", "pw",
                             authorized_keys=["ssh-ed25519 AAAAKEY medic"],
                             pw_hasher=lambda p: "HASH")
    assert 'authorized_keys = [ "ssh-ed25519 AAAAKEY medic" ]' in toml
    assert "[ssh]" in toml and "enabled = true" in toml


def test_flash_defaults_to_the_medics_key(monkeypatch):
    """flash() must include the medic's key without being asked."""
    import provisioning.pi_imager as pi
    monkeypatch.setattr(pi, "medic_public_key", lambda *a, **k: "ssh-ed25519 DEFAULTKEY m")
    monkeypatch.setattr(pi, "is_safe_target", lambda *a, **k: True)
    monkeypatch.setattr(pi, "carried_image", lambda *a, **k: "/tmp/os.img.xz")
    seen = []
    ok, msg = pi.flash("/dev/sdz", "h", "u", "p",
                       run_shell=lambda cmd: (seen.append(cmd), (0, ""))[1],
                       pw_hasher=lambda p: "HASH")
    assert ok, msg
    # the config is base64'd into the shell command, so decode to check
    import base64, re
    found = False
    for c in seen:
        for blob in re.findall(r"[A-Za-z0-9+/=]{40,}", c):
            try:
                if "DEFAULTKEY" in base64.b64decode(blob).decode("utf-8", "ignore"):
                    found = True
            except Exception:
                pass
    assert found, "medic key never reached the card"
def test_password_pair_must_match():
    ok, msg = pi.validate_new_password("correcthorse", "correcthorse")
    assert ok and msg == ""
    ok, msg = pi.validate_new_password("correcthorse", "correcthose")
    assert not ok and "match" in msg.lower()


def test_password_pair_rejects_empty():
    ok, msg = pi.validate_new_password("", "")
    assert not ok and "password" in msg.lower()


def test_password_pair_enforces_min_length():
    # matching but too short
    ok, msg = pi.validate_new_password("abc", "abc")
    assert not ok and "least" in msg.lower()
    # a matching password at the boundary passes
    exactly = "x" * pi.MIN_PASSWORD_LEN
    assert pi.validate_new_password(exactly, exactly)[0]


def test_password_mismatch_checked_before_length():
    # A short mismatch should report the mismatch (the more actionable error),
    # not the length — the operator needs to know they mistyped.
    ok, msg = pi.validate_new_password("abc", "abd")
    assert not ok and "match" in msg.lower()


def test_password_validates_without_a_confirm_field():
    """The imaging screen answers the mistyped-password risk with a Show/Hide
    reveal rather than a second field, so it calls this with one argument. The
    empty and length rules must still bite — a one-character password is as
    unrecoverable as a mistyped one once it is hashed onto the card."""
    ok, msg = pi.validate_new_password("correcthorse")
    assert ok and msg == ""
    ok, msg = pi.validate_new_password("")
    assert not ok and "password" in msg.lower()
    ok, msg = pi.validate_new_password("abc")
    assert not ok and "least" in msg.lower()


# --- noticing the card by itself -------------------------------------------
# Operator, 2026-08-06, watching the new card step: "is it possible for node
# medic to register that the SD card has been plugged in and start working by
# itself instead of having to press the write the card button".
# Answer built here: NOTICE automatically, WRITE deliberately.

def _lsblk(lines):
    def run(argv):
        if argv[:2] == ["findmnt", "-no"]:
            return (0, "/dev/mmcblk0p2")
        if argv[:2] == ["lsblk", "-no"] and "PKNAME" in argv:
            return (0, "mmcblk0")
        if argv[:2] == ["lsblk", "-dno"]:
            return (0, "\n".join(lines))
        return (0, "")
    return run


def test_no_card_says_so_plainly_instead_of_going_quiet():
    st = pi.card_status(_lsblk(["mmcblk0 59.5G disk mmc 0 ",
                                       "zram0 2G disk  0 "]))
    assert st["state"] == "none" and st["path"] == ""
    assert "slide one into the reader" in st["detail"].lower()


def test_one_card_is_named_so_the_operator_can_check_it():
    """The operator's only defence against writing the wrong card is being told
    which one it found — a size and a model they can compare with the thing in
    their hand."""
    st = pi.card_status(_lsblk([
        "mmcblk0 59.5G disk mmc 0 ",
        "sdb 29.7G disk usb 1 Generic MassStorageClass"]))
    assert st["state"] == "one"
    assert st["path"] == "/dev/sdb"
    assert "29.7G" in st["label"]
    assert "replaced" in st["detail"], "must say the card gets overwritten"


def test_TWO_cards_refuses_to_choose_between_them():
    """A medic that silently picks one of two cards will eventually pick the
    wrong one, and the operator will never know a choice was made. On this very
    bench a card that looked blank held the previous night's evidence."""
    st = pi.card_status(_lsblk([
        "mmcblk0 59.5G disk mmc 0 ",
        "sdb 29.7G disk usb 1 Generic MassStorageClass",
        "sdc 59.5G disk usb 1 Generic MassStorageClass"]))
    assert st["state"] == "several"
    assert st["path"] == "", "must NOT nominate a target when it cannot be sure"
    assert "take out the ones" in st["detail"]


def test_an_empty_reader_slot_is_not_mistaken_for_a_card():
    """A multi-slot reader offers empty slots as 0-byte disks (seen live: a
    Genesys reader showing /dev/sda 0B beside the real card)."""
    st = pi.card_status(_lsblk([
        "mmcblk0 59.5G disk mmc 0 ",
        "sda 0B disk usb 1 Generic MassStorageClass",
        "sdb 59.5G disk usb 1 Generic MassStorageClass"]))
    assert st["state"] == "one" and st["path"] == "/dev/sdb"


def test_the_medics_own_disk_is_never_offered_as_a_card():
    st = pi.card_status(_lsblk(["mmcblk0 59.5G disk mmc 0 "]))
    assert st["state"] == "none"


def test_no_next_step_line_is_a_paragraph():
    """2026-08-07, reported from the bench: step 3 rendered as
    "...a USB cable that carries DATA — a charge-only" and stopped — the label
    had a fixed height and silently cropped the wrap.

    The CLIPPING is fixed at source (ui.screens.pi_imager_screen._line now grows
    instead of cropping), so wrapping onto a second line is fine and this is no
    longer a hard width limit. What it still guards is the other half: a
    numbered step is an instruction, not a paragraph. Someone reading it has
    hardware in both hands.

    The in-the-Pi path is deliberately ONE instruction and slightly over a
    single line with a long board name — that is a considered operator decision
    ("numbering a one-item list is noise too"), not an oversight, so the ceiling
    allows it while still catching anything that turns into prose.
    """
    for via in (True, False):
        plan = pi.next_steps_after_imaging(via, pi_name="the Raspberry Pi 3 A+",
                                           hostname="h", wifi_ssid="net")
        for step in plan["steps"]:
            assert len(step) <= 95, f"this is a paragraph, not a step: {step!r}"


def test_the_charge_only_cable_trap_is_still_stated_somewhere():
    """Shortening the step must not lose the warning — it is the single most
    expensive thing learned on 2026-08-06."""
    plan = pi.next_steps_after_imaging(False, pi_name="the Pi")
    joined = " ".join(plan["steps"]).lower()
    assert "data cable" in joined
    assert "charge-only" in joined and "never appear" in joined


def test_the_screen_refuses_to_pick_between_two_cards():
    """The imager used to take targets[0] blindly. With two cards plugged in
    that silently writes one of them, and the operator never knows a choice was
    made. Source-level check because importing the screen needs Kivy."""
    src = open("ui/screens/pi_imager_screen.py").read()
    build = src[src.index("targets = pi_imager.list_target_disks()"):
                src.index("self._target = targets[0]")]
    assert "card_status()" in build
    assert '"several"' in build, "no branch for more than one card"
    assert "taken the others out" in build, "must offer a way forward"


def test_the_screen_notices_a_card_without_a_button_press():
    """Operator, 2026-08-06: "is it possible for node medic to register that the
    SD card has been plugged in and start working by itself instead of having to
    press the write the card button". The rescan button stays as a fallback."""
    src = open("ui/screens/pi_imager_screen.py").read()
    assert "_start_card_poll" in src and "_on_card_found" in src
    found = src[src.index("def _on_card_found"):src.index("def _offer_pi_as_reader")]
    # celebrates using whatever the animation offers, and never dies without it
    assert "mark_card_found" in found and "mark_connected" in found
    assert "_card_greeted" in found, "poll fires repeatedly; must not re-fire"


def test_noticing_a_card_does_not_start_writing_it():
    """The line that must never blur: detect automatically, destroy on purpose."""
    src = open("ui/screens/pi_imager_screen.py").read()
    found = src[src.index("def _on_card_found"):src.index("def _offer_pi_as_reader")]
    for destructive in ("_confirm(", "_do_write(", "flash(", "dd "):
        assert destructive not in found, (
            f"{destructive!r} in the auto-detect path — a card appearing must "
            "never begin an irreversible write")


# --- the four-minute window in which the medic said nothing (task #53) -------
# Found by the UI audit 2026-08-03. _write never called begin_activity(), so for
# the WHOLE write flash_in_progress() was False: no red "don't power off"
# banner, and the home screen's slide-to-power-off gave no warning — while the
# imaging screen's own callout was saying "don't power Node Medic off. A card
# interrupted part-way through has to be written again from the start."
#
# The tool contradicting itself, with a ruined card as the prize. It had already
# happened from the other direction: a UI restart killed a write 80 seconds in
# (2026-08-02). restart_ui.sh guards that path; this was the one still open.
#
# CI has no Kivy, so these assert the source contract.

def _imager_src():
    return open("ui/screens/pi_imager_screen.py").read()


def test_the_write_tells_the_app_it_is_running():
    src = _imager_src()
    assert "self._mark_activity(True" in src
    assert "begin_activity(" in src


def test_the_activity_is_released_when_the_write_ends():
    src = _imager_src()
    done = src[src.index("def _done(self, ok, msg)"):]
    done = done[:done.index("\n    def ")]
    assert "self._mark_activity(False)" in done


def test_the_release_happens_BEFORE_anything_that_can_raise():
    """begin_activity is a COUNTER. A write that ends without its matching
    end_activity leaves the medic believing a flash runs for ever — banner
    stuck, power-off blocked, next build refused, and only a restart to clear
    it. Failing to release is worse than never marking."""
    src = _imager_src()
    done = src[src.index("def _done(self, ok, msg)"):]
    done = done[:done.index("\n    def ")]
    # CODE only. The first version of this test searched the raw text and
    # matched the word "try:" inside the very comment explaining why the release
    # comes before the try — the same trap as grepping a docstring for the thing
    # it promises not to do.
    code = "\n".join(l.split("#")[0] for l in done.splitlines())
    release = code.index("_mark_activity(False)")
    first_try = code.find("try:")
    assert first_try == -1 or release < first_try


def test_every_exit_from_the_worker_reaches_done():
    """flash() can raise — its subprocess carries a 1800s timeout, and a card
    yanked mid-write surfaces as an OSError. An escaping exception would wedge
    the counter, which is exactly what the counter must never do."""
    src = _imager_src()
    work = src[src.index("def work():\n            # EVERY exit"):]
    work = work[:work.index("def _flash():")]
    assert "except Exception" in work
    assert "self._done(ok, msg)" in work


def test_the_operator_is_told_which_card_is_being_written():
    """A banner reading 'Working — please wait' over an unattended four-minute
    write says nothing about what would be lost by pulling the plug."""
    src = _imager_src()
    fn = src[src.index("def _mark_activity"):src.index("def _done(self, ok, msg)")]
    assert "Writing" in fn and "card" in fn
    assert "don't power off" in fn
