"""The first-boot config the carried image ACTUALLY reads.

Found at the bench, 2026-08-01: the medic wrote only custom.toml, but the
carried image (Raspberry Pi OS Trixie, pi-gen 2026-06-18) has cloud-init with
`datasource_list: [NoCloud, None]` / `seedfrom: file:///boot/firmware`, and
raspberrypi-sys-mods ships NO firstboot script. custom.toml was inert, so HOPE
booted with no user at all and refused every login.
"""

import base64

import pytest

# Test-only dependency. Skip rather than error where it is absent, so a bare
# environment can still run the rest of the suite — CI installs it so the
# YAML actually gets validated somewhere.
yaml = pytest.importorskip("yaml")

from provisioning import pi_imager as pi

KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFIXTUREKEY/xyz nodemedic@nodemedic"
HASH = "$6$FIXTUREsalt0002$fixture/hash.not.real.0002"


def _ud(**kw):
    kw.setdefault("hostname", "hope")
    kw.setdefault("username", "pi")
    kw.setdefault("password", "Fixture-pw-1?")
    kw.setdefault("pw_hasher", lambda p: HASH)
    kw.setdefault("authorized_keys", [KEY])
    return pi.build_cloud_init_user_data(**kw)


def test_user_data_is_valid_yaml_and_declares_cloud_config():
    text = _ud()
    assert text.startswith("#cloud-config")
    assert yaml.safe_load(text)          # would raise on malformed YAML


def test_it_creates_exactly_one_user_with_our_key():
    """Defining users: replaces cloud-init's default account, which is what we
    want — one account, ours."""
    doc = yaml.safe_load(_ud())
    assert len(doc["users"]) == 1
    u = doc["users"][0]
    assert u["name"] == "pi"
    assert u["ssh_authorized_keys"] == [KEY]
    assert u["passwd"] == HASH and u["lock_passwd"] is False


def test_the_password_hash_survives_yaml_intact():
    """A $6$ crypt is full of $ and / and . — unquoted it would mangle or be
    read as something else, and the account would be unusable."""
    doc = yaml.safe_load(_ud())
    assert doc["users"][0]["passwd"] == HASH


def test_the_user_can_actually_reach_the_radio_and_sudo():
    """An account with no dialout can't open the serial port; no sudo can't
    provision. Either makes the node useless."""
    u = yaml.safe_load(_ud())["users"][0]
    assert "dialout" in u["groups"] and "gpio" in u["groups"]
    assert "NOPASSWD" in u["sudo"]


def test_hostname_is_set():
    assert yaml.safe_load(_ud())["hostname"] == "hope"


def test_a_key_only_card_still_produces_a_valid_user():
    doc = yaml.safe_load(_ud(password=""))
    u = doc["users"][0]
    assert "passwd" not in u and u["ssh_authorized_keys"] == [KEY]


def test_ssh_password_auth_follows_the_flag():
    assert yaml.safe_load(_ud(enable_ssh=True))["ssh_pwauth"] is True
    assert yaml.safe_load(_ud(enable_ssh=False))["ssh_pwauth"] is False


# --- network-config ---------------------------------------------------------

def test_no_wifi_means_no_network_config_at_all():
    """The cable path deliberately puts no PSK on the card."""
    assert pi.build_cloud_init_network_config("", "") == ""


def test_no_wifi_psk_is_ever_written_to_the_boot_partition():
    """This used to build a netplan block carrying the PSK in clear text onto
    the FAT boot partition — the one partition on the card that mounts on any
    computer. It never worked (the medic's own card diagnosis reported "Wi-Fi
    details are on the card but were NEVER APPLIED", twice, 2026-08-09) and the
    Wi-Fi that does work is a 0600 root-owned NetworkManager file on the rootfs.

    So it bought nothing and cost the operator's home PSK to anyone who picked
    up a node."""
    assert pi.build_cloud_init_network_config("HomeNet", "sec ret") == ""
    toml = pi.build_custom_toml("h", "pi", "pw", pw_hasher=lambda p: "x")
    assert "sec ret" not in toml and "password_encrypted = false" not in toml


# --- it actually reaches the card ------------------------------------------

def _decoded_writes(cmds):
    """filename -> content, for every base64 tee in the command list."""
    out = {}
    for c in cmds:
        if "base64 -d | sudo tee" not in c:
            continue
        blob = c.split("echo ")[1].split(" |")[0].strip("'")
        name = c.rsplit("tee ", 1)[1].split()[0].rsplit("/", 1)[-1]
        out[name] = base64.b64decode(blob).decode()
    return out


def test_the_seed_files_are_written_to_the_boot_partition():
    cmds = pi.apply_config_commands("/dev/sdb", "toml=1",
                                    user_data=_ud(),
                                    network_config="version: 2\n")
    written = _decoded_writes(cmds)
    assert "user-data" in written, "cloud-init would find no seed"
    assert "network-config" in written
    assert written["user-data"].startswith("#cloud-config")


def test_empty_seed_files_are_not_written():
    written = _decoded_writes(pi.apply_config_commands("/dev/sdb", "toml=1"))
    assert "user-data" not in written and "network-config" not in written


def test_custom_toml_is_still_written_alongside():
    """The two mechanisms are mutually inert, so writing both makes one card
    work on either kind of image rather than betting on which we carry."""
    written = _decoded_writes(pi.apply_config_commands("/dev/sdb", "toml=1",
                                                       user_data=_ud()))
    assert written.get("custom.toml") == "toml=1"


def test_flash_puts_a_cloud_init_seed_on_the_card(monkeypatch):
    """The regression that cost a 9-minute write and an unreachable Pi. The
    seed now travels to the root helper in its config rather than as a tee
    command, but it must still get there."""
    from provisioning import pi_imager
    captured = {}

    def medic_run(argv, **kw):
        if argv[:2] == ["findmnt", "-no"]:
            return (0, "/dev/mmcblk0p2")
        if argv[:2] == ["lsblk", "-no"] and "PKNAME" in argv:
            return (0, "mmcblk0")
        if argv[:2] == ["lsblk", "-dno"]:
            return (0, "mmcblk0 59.5G disk mmc  0 \nsdb 29.7G disk usb  1 Reader")
        return (0, "")

    def shell(cmd):
        return (0, "")

    monkeypatch.setattr(pi_imager, "write_card_config",
                        lambda cfg, path="/tmp/x": (captured.update(cfg), path)[1])

    ok, _ = pi_imager.flash("/dev/sdb", "hope", "pi", "Fixture-pw-1?",
                            image_path="/tmp/x.img.xz", run=medic_run,
                            run_shell=shell, pw_hasher=lambda p: HASH,
                            authorized_keys=[KEY])
    assert ok
    doc = yaml.safe_load(captured["user_data"])
    assert doc["users"][0]["ssh_authorized_keys"] == [KEY]
    assert captured["meta_data"], "cloud-init needs a fresh instance_id too"
