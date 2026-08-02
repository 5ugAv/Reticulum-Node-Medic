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
