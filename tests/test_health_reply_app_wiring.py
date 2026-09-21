"""The medic's side of the unicast health reply, pinned in code
(docs/HEALTH_REPLY_UNICAST.md, 2026-09-21): the reply destination is set
up on every attach, replies are verified and ingested as the node's own
word, the poll runs in two phases with the fallback never sent before the
node's rate limiter would let it through, and a relayed signal is called
the relay's."""
import re

APP = "ui/app.py"


def _body(name):
    src = open(APP).read()
    m = re.search(r"    def " + re.escape(name) + r"\(.*?(?=\n    def |\n    _[a-z_]+ = |\Z)",
                  src, re.S)
    assert m, f"{name} missing"
    return m.group(0)


def test_reply_destination_is_set_up_on_every_attach_and_owned():
    src = open(APP).read()
    i = src.index("register_announce_handler(_HealthHandler())")
    assert "_setup_health_reply(RNS, _log)" in src[i:i + 400]
    body = _body("_setup_health_reply")
    assert "load_or_create_identity(RNS)" in body
    assert "set_packet_callback(self._on_health_reply)" in body
    assert "set_own_destinations(" in body and "set_own_identities(" in body
    assert 'why="attach"' in body


def test_the_keeper_reannounces_when_rnsd_forgot_and_stays_quiet_after_a_poll():
    body = _body("_keep_reply_dest_announced")
    assert "_reply_dest_known_to_rnsd(RNS)" in body
    assert "REPLY_ANNOUNCE_QUIET_AFTER_POLL_S" in body
    assert "REPLY_ANNOUNCE_EVERY_S" in body
    check = _body("_reply_dest_known_to_rnsd")
    assert "rnpath -t --json" in check and "hops" in check


def test_a_reply_is_verified_fresh_ingested_as_reply_and_claims_its_poll():
    body = _body("_on_health_reply")
    assert "verify_reply(" in body and "RNS.Identity.recall" in body
    assert "uptime_is_fresh(" in body
    assert 'source="reply"' in body
    assert "_pending_polls.claim(nonce, dest)" in body
    assert "record_probe(dest_hex, ok=True" in body
    assert "late_reply_note = note" in body


def test_the_poll_speaks_to_me_first_and_falls_back_after_the_limiter():
    body = _body("_ping_node")
    assert "build_request_to(bytes(rd.hash))" in body
    assert "_pending_polls.add(" in body
    assert "unicast_wait_s(hops)" in body and "announce_wait_s(hops)" in body
    assert "build_fallback_request()" in body
    # the fallback is sent only AFTER the unicast window, inside _heard
    assert body.index("unicast_wait_s(hops)") < body.index("build_fallback_request()")


def test_a_relayed_signal_is_called_the_relays():
    body = _body("_ping_node")
    assert "Signal of the relay's transmission" in body


def test_the_node_page_shows_a_late_reply():
    src = open("ui/screens/node_detail_screen.py").read()
    assert "_show_late_reply()" in src and "late_reply_note" in src
