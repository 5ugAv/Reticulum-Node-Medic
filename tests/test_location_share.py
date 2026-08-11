"""Publishing a node's position — and, by default, not.

These tests exist because the failure this feature can produce is invisible
from the medic: a node configured to share that publishes nothing, or a node
publishing after somebody thought they had turned it off. Both look identical
on the screen unless something checks.

Coordinates here are the declared synthetics from tests/test_privacy_tree.py —
no real place appears in this file.
"""

import pytest

from monitor import geo, kin_roster, location_share as ls
from monitor.registry import NodeRegistry

# declared synthetic coordinates (see tests/test_privacy_tree.py)
LAT, LON = -37.512345, 145.523456
HASH = "aabbccddeeff00112233445566778899"

CONFIG = """\
[reticulum]
enable_transport = Yes

[logging]
loglevel = 4

[interfaces]

  [[RNode LoRa Interface]]
    type = RNodeInterface
    enabled = Yes
    port = /dev/rnode
    frequency = 915125000
"""


# --- the default is silence -------------------------------------------------

def test_default_policy_is_hidden():
    assert ls.DEFAULT_POLICY == ls.HIDDEN
    assert ls.is_shared(ls.HIDDEN) is False


@pytest.mark.parametrize("value", [None, "", "yes", "true", "1", "SHARE",
                                   "approximate", 0, [], {}])
def test_anything_unrecognised_reads_as_hidden(value):
    """An unreadable setting is not consent. Every stored value that is not
    exactly a known policy must fall to hidden — including the ones that LOOK
    affirmative, because a config written by an older or a different tool
    saying "yes" has not been agreed to by this operator."""
    assert ls.normalise(value) == ls.HIDDEN
    assert ls.is_shared(ls.normalise(value)) is False


def test_true_is_accepted_as_approximate():
    """The one legacy shape that IS an answer: a bool from an earlier record."""
    assert ls.normalise(True) == ls.APPROX


# --- what leaves the device is never the truth ------------------------------

def test_public_pin_is_fuzzed_and_never_the_real_point():
    pin = ls.public_pin(LAT, LON, "NODE")
    assert pin is not None
    flat, flon, radius = pin
    assert (flat, flon) != (LAT, LON)
    assert radius == geo.FUZZ_RADIUS_M
    # displaced, but still the right neighbourhood — a pin in the wrong suburb
    # is as useless as no pin
    assert abs(flat - LAT) < 0.02 and abs(flon - LON) < 0.02


def test_public_pin_is_stable_for_a_node():
    """A re-rolling offset could be averaged back to the truth by anyone who
    collects enough announces. Same node, same fake point, forever."""
    assert ls.public_pin(LAT, LON, "NODE") == ls.public_pin(LAT, LON, "NODE")
    assert ls.public_pin(LAT, LON, "OTHER") != ls.public_pin(LAT, LON, "NODE")


def test_public_pin_is_none_without_coordinates():
    """Never 0,0 — an island in the Gulf of Guinea is a real map pin."""
    assert ls.public_pin(None, None, "NODE") is None
    assert ls.public_pin(LAT, None, "NODE") is None


def test_stranger_view_names_everything_in_the_packet_not_just_position():
    """The announce carries the node's NAME, radio settings and transport
    identity too (RNS/Discovery.py). Agreeing to a pin is not agreeing to
    those unless the screen said so."""
    view = ls.stranger_view(ls.APPROX, "NODE", LAT, LON, "NODE")
    assert view["shared"] is True
    blob = " ".join(view["items"]).lower()
    assert "name" in blob
    assert "radio" in blob
    assert "transport identity" in blob
    assert "800" in blob            # the distance, in the operator's words


def test_stranger_view_when_hidden_lists_nothing():
    view = ls.stranger_view(ls.HIDDEN, "NODE", LAT, LON, "NODE")
    assert view["shared"] is False
    assert view["items"] == []
    assert view["pin"] is None


def test_the_recall_sentence_exists_and_says_so():
    assert "cannot be taken back" in ls.cannot_be_recalled()


# --- the RNS config a Pi node actually reads --------------------------------

def test_hidden_config_carries_no_position():
    out, notes = ls.apply_to_reticulum_config(CONFIG, policy=ls.HIDDEN)
    assert "discoverable" not in out
    assert "latitude" not in out
    assert notes


def test_shared_config_matches_the_rns_convention():
    """The keys RNS 1.3.7 reads out of an interface section (Reticulum.py
    :824-856) — spelt exactly, or the node silently publishes nothing."""
    out, notes = ls.apply_to_reticulum_config(
        CONFIG, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON,
        node_key="NODE")
    for key in ("discoverable = Yes", "discovery_name = NODE",
                "announce_interval = 360", "latitude = ", "longitude = "):
        assert key in out, key
    # and it says which stanza it landed in, so an operator can check
    assert notes and "RNode LoRa Interface" in notes[0]


def test_shared_config_publishes_the_fuzzed_point_not_the_real_one():
    out, _ = ls.apply_to_reticulum_config(
        CONFIG, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON,
        node_key="NODE")
    assert f"latitude = {LAT}" not in out
    assert f"longitude = {LON}" not in out
    on_file = ls.shared_position_in_config(out)
    assert (on_file["lat"], on_file["lon"]) != (LAT, LON)
    assert abs(on_file["lat"] - LAT) < 0.02


def test_sharing_sets_gateway_mode_so_the_node_keeps_forwarding_announces():
    """THE TRAP. `discoverable` alone makes RNS reassign an RNodeInterface to
    access-point mode (Reticulum.py:856-864), and an AP interface stops
    rebroadcasting other nodes' announces (Transport.py:1224). On the relay the
    mesh routes through, that trades the network for a dot on a website."""
    out, _ = ls.apply_to_reticulum_config(
        CONFIG, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON)
    assert "mode = gateway" in out


def test_the_written_block_parses_as_reticulum_reads_it():
    """Not "it looks like config" — RNS's OWN parser, with the accessors RNS
    itself calls on those keys."""
    configobj = pytest.importorskip("RNS.vendor.configobj")
    out, _ = ls.apply_to_reticulum_config(
        CONFIG, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON)
    section = configobj.ConfigObj(
        out.splitlines())["interfaces"]["RNode LoRa Interface"]
    assert section.as_bool("discoverable") is True
    assert isinstance(section.as_float("latitude"), float)
    assert section.as_int("announce_interval") == 360
    assert section["mode"] == "gateway"


def test_turning_sharing_off_restores_the_config_exactly():
    """Off has to be a thing the tool PRODUCES, not merely a thing it declines
    to write — otherwise withdrawing a position leaves the node announcing."""
    shared, _ = ls.apply_to_reticulum_config(
        CONFIG, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON)
    back, _ = ls.apply_to_reticulum_config(shared, policy=ls.HIDDEN)
    assert back == CONFIG
    assert ls.shared_position_in_config(back) is None


def test_applying_twice_writes_one_block():
    once, _ = ls.apply_to_reticulum_config(
        CONFIG, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON)
    twice, _ = ls.apply_to_reticulum_config(
        once, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON)
    assert twice.count(ls.BLOCK_START) == 1
    assert twice == once


def test_sharing_without_coordinates_writes_nothing_and_says_so():
    out, notes = ls.apply_to_reticulum_config(CONFIG, policy=ls.APPROX,
                                              name="NODE")
    assert "discoverable" not in out
    assert any("coordinates" in n for n in notes)


def test_no_announceable_interface_is_reported_not_swallowed():
    """A node whose only interface RNS will never announce on is the exact
    silent failure this module exists to prevent."""
    cfg = CONFIG.replace("RNodeInterface", "AutoInterface")
    out, notes = ls.apply_to_reticulum_config(
        cfg, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON)
    assert "discoverable" not in out
    assert any("announce on" in n for n in notes)


def test_a_hand_written_key_is_left_alone_and_flagged():
    cfg = CONFIG + "    mode = full\n"
    _out, notes = ls.apply_to_reticulum_config(
        cfg, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON)
    assert any("mode = full" in n for n in notes)


def test_find_target_interface_picks_an_announceable_type():
    assert ls.find_target_interface(CONFIG) == "RNode LoRa Interface"
    assert ls.find_target_interface(
        CONFIG.replace("RNodeInterface", "AutoInterface")) is None


# --- the collector uplink is a SEPARATE decision ----------------------------

def test_rmap_uplink_is_not_written_by_sharing():
    out, _ = ls.apply_to_reticulum_config(
        CONFIG, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON)
    assert ls.RMAP_HOST not in out


def test_rmap_uplink_block_is_a_tcp_client_interface():
    block = "\n".join(ls.rmap_uplink_block())
    assert "type = TCPClientInterface" in block
    assert f"target_host = {ls.RMAP_HOST}" in block
    assert f"target_port = {ls.RMAP_PORT}" in block
    assert "public IP" in block          # says what it discloses


# --- pushing to a live node: believe the read-back, not the write -----------

class FakeNode:
    """A node that stores a config file and answers shell-ish commands."""

    def __init__(self, config=CONFIG, active="active", writable=True,
                 restart_code=0):
        self.config = config
        self.active = active
        self.writable = writable
        self.restart_code = restart_code
        self.commands = []

    def run(self, cmd):
        self.commands.append(cmd)
        if "restart rnsd" in cmd:
            return (self.restart_code, "",
                    "" if self.restart_code == 0
                    else "sudo: a password is required")
        if cmd.startswith("cat > "):
            if not self.writable:
                return (1, "", "Read-only file system")
            body = cmd.split("<<'RTTEOF'\n", 1)[1]
            self.config = body[:body.rindex("\nRTTEOF")] + "\n"
            return (0, "", "")
        if cmd.startswith("cat "):
            return (0, self.config, "")
        if "is-active" in cmd:
            return (0, self.active, "")
        return (0, "", "")


def test_push_writes_reads_back_and_restarts():
    node = FakeNode()
    ok, detail = ls.push_to_node(node.run, policy=ls.APPROX, name="NODE",
                                 lat=LAT, lon=LON, node_key="NODE")
    assert ok is True
    assert "discoverable = Yes" in node.config
    assert any("restart rnsd" in c for c in node.commands)
    assert "cannot be checked from here" in detail   # never claims a map pin


def test_push_reports_a_failed_write_instead_of_a_success():
    node = FakeNode(writable=False)
    ok, detail = ls.push_to_node(node.run, policy=ls.APPROX, name="NODE",
                                 lat=LAT, lon=LON)
    assert ok is False
    assert "Read-only" in detail


def test_push_fails_when_rnsd_does_not_come_back():
    """A node whose mesh daemon is dead after the change is not a success,
    however well the file was written."""
    node = FakeNode(active="failed")
    ok, detail = ls.push_to_node(node.run, policy=ls.APPROX, name="NODE",
                                 lat=LAT, lon=LON)
    assert ok is False
    assert "failed" in detail and "before you leave" in detail


def test_push_can_withdraw_a_position():
    shared, _ = ls.apply_to_reticulum_config(
        CONFIG, policy=ls.APPROX, name="NODE", lat=LAT, lon=LON)
    node = FakeNode(config=shared)
    ok, detail = ls.push_to_node(node.run, policy=ls.HIDDEN, name="NODE",
                                 lat=LAT, lon=LON)
    assert ok is True
    assert ls.shared_position_in_config(node.config) is None
    assert "no position" in detail


def test_push_does_not_restart_when_nothing_changed():
    node = FakeNode()
    ok, _ = ls.push_to_node(node.run, policy=ls.HIDDEN, name="NODE",
                            lat=LAT, lon=LON)
    assert ok is True
    assert not any("restart" in c for c in node.commands)


def test_push_reports_an_unreadable_config_rather_than_guessing():
    def run(cmd):
        return (1, "", "No such file or directory")
    ok, detail = ls.push_to_node(run, policy=ls.APPROX, lat=LAT, lon=LON)
    assert ok is False
    assert "Could not read" in detail


# --- the honest status line -------------------------------------------------

def test_status_distinguishes_recorded_from_applied():
    recorded = ls.status_line(ls.APPROX, False, LAT, LON)
    applied = ls.status_line(ls.APPROX, True, LAT, LON)
    assert "records only" in recorded and "announcing nothing" in recorded
    assert "written to the node" in applied


def test_status_warns_that_an_unapplied_off_is_still_sharing():
    """The dangerous state: the operator has turned it off on a screen and the
    node on the roof has not been told."""
    line = ls.status_line(ls.HIDDEN, False, LAT, LON)
    assert "STILL SHARING" in line


def test_status_without_coordinates_says_there_is_nothing_to_publish():
    assert "nothing to publish" in ls.status_line(ls.APPROX, False, None, None)


# --- finding the node again -------------------------------------------------

def test_node_address_from_certs_prefers_the_newest_match():
    certs = [{"name": "NODE", "ssh_address": "old.local", "issued_at": 1.0},
             {"name": "NODE", "ssh_address": "new.local", "issued_at": 2.0},
             {"name": "OTHER", "ssh_address": "no.local", "issued_at": 9.0}]
    assert ls.node_address_from_certs(certs, name="NODE") == "new.local"


def test_node_address_matches_on_identity_too():
    certs = [{"identity_hash": HASH, "ip_addresses": ["10.0.0.9"]}]
    assert ls.node_address_from_certs(certs, identity_hash=HASH) == "10.0.0.9"


def test_node_address_is_empty_when_unknown():
    assert ls.node_address_from_certs([], name="NODE") == ""
    assert ls.node_address_from_certs([{"name": "NODE"}], name="NODE") == ""


# --- the registry remembers, and remembers cautiously -----------------------

def test_a_new_record_shares_nothing():
    reg = NodeRegistry()
    rec = reg.register(HASH, name="NODE", lat=LAT, lon=LON)
    assert rec.share_location == ls.HIDDEN
    assert rec.public_pin() is None


def test_register_without_an_answer_never_changes_an_existing_one():
    """Every pre-existing caller passes nothing. None must mean "no opinion",
    not "reset" and certainly not "enable"."""
    reg = NodeRegistry()
    reg.register(HASH, name="NODE")
    reg.set_share_location(HASH, ls.APPROX, now=1.0)
    reg.register(HASH, name="NODE", lat=LAT, lon=LON)
    assert reg.get(HASH).share_location == ls.APPROX


def test_changing_the_decision_logs_it_and_clears_applied():
    reg = NodeRegistry()
    reg.register(HASH, name="NODE", lat=LAT, lon=LON)
    reg.set_share_location(HASH, ls.APPROX, now=10.0)
    reg.mark_share_applied(HASH, now=11.0)
    assert reg.get(HASH).share_applied_at == 11.0
    reg.set_share_location(HASH, ls.HIDDEN, now=12.0)
    rec = reg.get(HASH)
    # the node has NOT been told yet — saying otherwise is the dangerous lie
    assert rec.share_applied_at is None
    assert any(e.kind == "location" for e in rec.events)


def test_record_public_pin_only_exists_when_shared():
    reg = NodeRegistry()
    reg.register(HASH, name="NODE", lat=LAT, lon=LON)
    assert reg.get(HASH).public_pin() is None
    reg.set_share_location(HASH, ls.APPROX, now=1.0)
    pin = reg.get(HASH).public_pin()
    assert pin is not None and (pin[0], pin[1]) != (LAT, LON)


def test_the_decision_survives_a_restart():
    reg = NodeRegistry()
    reg.register(HASH, name="NODE", lat=LAT, lon=LON)
    reg.set_share_location(HASH, ls.APPROX, now=1.0)
    reg.mark_share_applied(HASH, now=2.0)
    back = NodeRegistry.from_dict(reg.to_dict())
    assert back.get(HASH).share_location == ls.APPROX
    assert back.get(HASH).share_applied_at == 2.0


def test_a_registry_written_before_this_field_existed_reads_as_hidden():
    reg = NodeRegistry.from_dict(
        {"nodes": [{"dst_hash": HASH, "name": "NODE", "lat": LAT, "lon": LON}]})
    assert reg.get(HASH).share_location == ls.HIDDEN


def test_birth_certificate_carries_the_decision_into_the_registry():
    reg = NodeRegistry()
    reg.register_from_birth_certificate(
        {"identity_hash": HASH,
         "location": {"lat": LAT, "lon": LON, "share_location": ls.APPROX}},
        name="NODE", now=1.0)
    assert reg.get(HASH).share_location == ls.APPROX


def test_kin_roster_round_trips_the_decision(tmp_path):
    path = str(tmp_path / "kin.json")
    kin_roster.register(HASH, "NODE", "pi_propagation", lat=LAT, lon=LON,
                        path=path)
    assert kin_roster.load_roster(path)[HASH].get("share_location") is None
    kin_roster.set_share_location(HASH, ls.APPROX, path=path)
    assert kin_roster.load_roster(path)[HASH]["share_location"] == ls.APPROX
    reg = NodeRegistry()
    reg.set_kin_roster(kin_roster.load_roster(path))
    assert reg.get(HASH).share_location == ls.APPROX


def test_reach_note_says_a_lora_only_node_may_never_reach_a_map():
    """Configured-to-share and visible-on-a-map are different states. A tool
    that runs them together produces a node its operator believes is on a map
    for months."""
    note = ls.reach_note()
    assert "LoRa" in note
    assert "cannot see" in note


def test_push_does_not_call_a_refused_restart_a_success():
    """Scoped sudo refuses the restart, the OLD rnsd keeps running the OLD
    config, and `systemctl is-active` still answers "active". Asking only
    whether rnsd is up would report a change that never happened."""
    node = FakeNode(restart_code=1)
    ok, detail = ls.push_to_node(node.run, policy=ls.APPROX, name="NODE",
                                 lat=LAT, lon=LON)
    assert ok is False
    assert "OLD config" in detail
    assert "password is required" in detail
