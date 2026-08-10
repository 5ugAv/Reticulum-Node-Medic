"""The Wi-Fi has to be PUT on the card, not requested of it.

rootfs_user exists because both boot-time config mechanisms are silent no-ops
on the carried image: custom.toml is inert (no raspberrypi-sys-mods/firstboot)
and cloud-init user-data is inert too. The ACCOUNT got a direct-to-rootfs
fallback because of that. The WI-FI never did.

So it never worked. Two cards in a row, 2026-08-09, both diagnosed by the medic
as "Wi-Fi details are on the card but were NEVER APPLIED", and a first boot on a
clean supply changed nothing — there was nothing to change, because no agent on
that image reads the request.
"""
from provisioning import rootfs_wifi as rw


def test_no_ssid_writes_nothing():
    """The cable-birth path deliberately puts no PSK on the card at all, and
    that choice must survive this."""
    assert rw.activate_commands("/mnt", "", "") == []


def test_the_connection_names_the_network_and_secures_it():
    text = rw.connection_file("Home_5g", "hunter2")
    assert "ssid=Home_5g" in text
    assert "key-mgmt=wpa-psk" in text and "psk=hunter2" in text
    assert "type=wifi" in text and "autoconnect=true" in text
    assert "method=auto" in text, "a node on someone's LAN wants DHCP"


def test_the_uuid_is_stable_not_random():
    """The same card written twice must not accumulate two connections for one
    network, and a fixed value keeps the file byte-comparable."""
    assert rw.connection_uuid("Home_5g") == rw.connection_uuid("Home_5g")
    assert rw.connection_uuid("Home_5g") != rw.connection_uuid("Other")


def test_permissions_are_set_or_networkmanager_ignores_the_file():
    """NM REFUSES a connection file that is not 0600 and root-owned, and says
    so only in its own log. A world-readable file is the same as no file —
    which is the exact failure being fixed."""
    cmds = rw.activate_commands("/mnt", "Home_5g", "hunter2")
    joined = " ".join(cmds)
    assert "chmod 0600" in joined
    assert "chown 0:0" in joined


def test_the_regulatory_country_is_written():
    """5 GHz channels are unusable until the country is set, so a card aimed at
    a 5 GHz-only SSID joins nothing without it. The operator's own network is
    '..._5g', so this is not hypothetical."""
    cmds = rw.activate_commands("/mnt", "Home_5g", "hunter2", country="AU")
    joined = " ".join(cmds)
    assert rw.WPA_CONF in joined
    assert "country=AU" in rw.wpa_country_file("au"), "and it is upper-cased"


def test_secrets_never_ride_in_a_heredoc():
    """An SSID or PSK is operator text; a heredoc would let it terminate the
    document — the same injection already fixed for the card-prepare path."""
    cmds = rw.activate_commands("/mnt", "Home_5g", "hunter2")
    joined = "\n".join(cmds)
    assert "<<" not in joined, "no heredocs"
    assert "base64 -d" in joined
    assert "hunter2" not in joined, "the secret is encoded, not pasted inline"


def test_it_writes_under_the_mount_never_the_medics_own_root():
    cmds = rw.activate_commands("/tmp/rnm-piroot", "Home_5g", "hunter2")
    for c in cmds:
        assert "/tmp/rnm-piroot" in c, f"escaped the mount: {c}"


def test_a_slash_in_an_ssid_cannot_become_a_path():
    cmds = rw.activate_commands("/mnt", "a/b", "x")
    assert any("a_b.nmconnection" in c for c in cmds)


def test_it_can_prove_what_it_wrote():
    """The same standard rootfs_user holds itself to: verify before the card
    leaves, rather than hope."""
    cmds = rw.verify_commands("/mnt", "Home_5g")
    assert any("stat" in c for c in cmds)


# --- the root helper must not drift from this module -----------------------
#
# assets/scripts/prepare_card.py runs as ROOT and deliberately imports nothing
# from the repo — that is the privilege boundary — so the same logic exists
# twice on purpose. Twice on purpose is fine; twice by accident is a bug that
# only shows up on a card.

def _helper():
    import importlib.util
    from tests.srcutil import ROOT
    import os
    spec = importlib.util.spec_from_file_location(
        "prepare_card_helper", os.path.join(ROOT, "assets/scripts/prepare_card.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_root_helper_writes_the_same_connection():
    h = _helper()
    assert h.wifi_connection_text("Home_5g", "hunter2") == \
        rw.connection_file("Home_5g", "hunter2")


def test_the_root_helper_writes_the_same_country_file():
    h = _helper()
    assert h.wifi_country_text("au") == rw.wpa_country_file("au")


def test_the_root_helper_locks_the_file_down_and_skips_empty_ssids():
    from tests.srcutil import src
    text = src("assets/scripts/prepare_card.py")
    body = text[text.index("def write_wifi("):]
    body = body[:body.index("\ndef ", 1)] if "\ndef " in body[1:] else body
    assert "0o600" in body, "NetworkManager ignores a looser file"
    assert "os.chown(path, 0, 0)" in body
    assert "if not ssid" in body, "cable-birth cards carry no PSK at all"


def test_the_imager_actually_hands_the_wifi_to_the_helper():
    """The mirrored writer is useless if nothing passes it the network."""
    from tests.srcutil import func_source
    src = func_source("provisioning/pi_imager.py", "flash")
    assert '"wifi_ssid": wifi_ssid' in src
    assert '"wifi_psk": wifi_password' in src
    assert '"wifi_country": wifi_country' in src


def test_a_failed_connection_write_leaves_no_decoy():
    """Found by running the INSTALLED helper against a real filesystem: chown
    failed, and because it all sat in one try/except the country file was never
    written AND a connection file was left with the wrong owner. NetworkManager
    ignores such a file without a word, so the next person finds "the Wi-Fi is
    on the card" and believes it."""
    from tests.srcutil import src
    body = src("assets/scripts/prepare_card.py")
    body = body[body.index("def write_wifi("):]
    body = body[:body.index("\ndef ", 1)] if "\ndef " in body[1:] else body
    assert "os.remove(path)" in body, "a half-made connection file must go"
    assert body.count("except Exception") >= 2, \
        "the country file must not be lost to a connection-file failure"
    assert "5 GHz networks will be unusable" in body, \
        "and a missing country has its own consequence, so it gets its own words"


# --- the radio ships switched off ------------------------------------------

def test_the_regdom_goes_on_the_kernel_command_line():
    """The months-old mystery, closed on 2026-08-10: the Wi-Fi radio ships
    SOFT-BLOCKED by rfkill, and a blocked radio joins nothing however perfect
    its keyfile. Every earlier theory was about credentials. The regdom on the
    kernel command line is what releases it, and it is read at every boot — so
    it survives the power cut a field node actually gets."""
    from provisioning.rootfs_wifi import regdom_cmdline
    out = regdom_cmdline("console=tty1 rootwait modules-load=dwc2,g_ether\n", "AU")
    assert "cfg80211.ieee80211_regdom=AU" in out
    assert out.endswith("\n"), "cmdline.txt keeps its trailing newline"
    assert "modules-load=dwc2,g_ether" in out, "must not disturb the gadget"


def test_a_second_write_does_not_stack_two_regdoms():
    from provisioning.rootfs_wifi import regdom_cmdline
    once = regdom_cmdline("a b", "GB")
    twice = regdom_cmdline(once, "AU")
    assert twice.count("cfg80211.ieee80211_regdom=") == 1
    assert "AU" in twice and "GB" not in twice


def test_no_country_changes_nothing():
    """The cable-birth path deliberately carries no network at all."""
    from provisioning.rootfs_wifi import regdom_cmdline
    assert regdom_cmdline("a b\n", "") == "a b\n"


def test_networkmanager_is_told_its_radio_is_on():
    """Its log named two separate refusals — the killswitch AND "disabled by
    state file". Unblocking the hardware is not enough on its own."""
    from provisioning.rootfs_wifi import nm_state_file, NM_STATE
    assert "WirelessEnabled=true" in nm_state_file()
    assert NM_STATE.endswith("NetworkManager.state")


def test_the_card_writer_does_both_and_reads_them_back():
    src = open("assets/scripts/prepare_card.py").read()
    assert "cmdline_with_regdom" in src, "the regdom must reach the real card"
    assert "NetworkManager.state" in src
    # read-back, like the gadget's two halves already are
    assert "the wireless regulatory domain" in src, \
        "a write that is not read back is a claim, not a fact"
