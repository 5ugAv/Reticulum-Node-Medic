"""Readiness ledger, 2026-10-05 early, batch ten — Backpack-mode chat truth,
the CHAT tour step, and message order by arrival rather than two clocks."""
import json
import os

from tests.srcutil import ROOT, src
from tests.test_chat_service import svc  # noqa: F401  (the bench fixture)


# -- #76 / #185 ------------------------------------------------------------

def test_in_backpack_nothing_is_held_and_the_words_say_so(svc):
    import monitor.lxmf_chat as lc
    from tests.test_chat_service import FakeIdentity, FakeTransport, PEER, PEER_BYTES, _wait
    svc._propagation_probe = lambda: False               # the medic is in Backpack
    svc._refresh_propagation()
    assert svc.propagation_on is False
    FakeIdentity.known[PEER_BYTES] = FakeIdentity("pe")
    FakeTransport.paths.discard(PEER_BYTES)              # no path -> would be PROPAGATED
    assert svc.send(PEER, "anyone home?") is not None
    assert _wait(lambda: svc.store.thread(PEER)[0]["state"] == lc.UNHELD)
    assert svc._router.outbound == []                    # never handed to the router
    assert "Backpack" in lc.STATE_WORDS[lc.UNHELD]
    # it is retried like a failed message the moment the peer is heard
    assert [m["text"] for m in svc.store.failed_to(PEER)] == ["anyone home?"]
    # and the sync is not asked for
    assert svc.tick(force=True) is False


def test_the_chat_screen_names_backpack_instead_of_held_here():
    c = src("ui/screens/chat_screen.py")
    assert 'getattr(svc, "propagation_on", None) is False' in c
    assert "nothing holds it in Backpack mode" in c
    assert 'tr("Propagation node off — Backpack mode holds no messages")' in c
    s = src("monitor/chat_service.py")
    assert "def _propagation_probe()" in s and "propagation_running(LocalConnection())" in s
    assert "self._refresh_propagation()" in s


# -- #151 ------------------------------------------------------------------

def test_the_tour_has_a_chat_screen_on_its_own_painted_card():
    import ui.setup_flow as sf
    from ui.home_zones import card_rect
    step = sf.step_for(sf.TOUR_CHAT, sf.SetupState())
    assert step["opens"] == "chat" and step["poster_card"] == "chat"
    assert card_rect("chat") is not None
    keys = [s["key"] for s in sf.setup_steps(sf.SetupState()) if s["part"] == sf.TOUR]
    assert keys.index(sf.TOUR_TRIAGE) < keys.index(sf.TOUR_CHAT) < keys.index(sf.TOUR_PROBE)
    assert "six modes" not in src("ui/setup_flow.py")
    for code in ("es", "fr", "de", "ja", "ru", "pl", "id", "sv"):
        with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"), encoding="utf-8") as f:
            cat = json.load(f)
        assert step["title"] in cat and step["body"] in cat, code


# -- #190 ------------------------------------------------------------------

def test_threads_follow_arrival_not_the_senders_clock(tmp_path):
    from monitor.lxmf_chat import MessageStore
    store = MessageStore(str(tmp_path / "chat"))
    peer = "c" * 32
    store.add_outgoing(peer, "first (medic clock 1000)", ts=1000.0)
    store.add_incoming(peer, "reply (phone clock says 900)", ts=900.0)
    texts = [m["text"] for m in store.thread(peer)]
    assert texts == ["first (medic clock 1000)", "reply (phone clock says 900)"]
    conv = store.conversations()[0]
    assert conv.last_text == "reply (phone clock says 900)"   # the latest by arrival
    assert [m.get("seq") for m in store.thread(peer)] == [1, 2]
