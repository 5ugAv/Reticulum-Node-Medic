"""Readiness ledger, 2026-10-05 early, batch nine — the setup wizard's pattern
step, the birth guide's manual pick and reading step, the phone hand-off,
the walkthrough resume, Chat's delete gesture and outgoing hashes, the Clone
flow's buttons, the language toast, PROBE's refusals."""
import json
import os

from tests.srcutil import ROOT, src

SHIPPED = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")


def test_the_pattern_step_opens_on_the_first_drawing_every_visit():
    w = src("ui/screens/setup_wizard_screen.py")
    head = w[w.index("def _render_pattern"):w.index("def _render_pattern") + 400]
    assert "self._pattern_first = None" in head


def test_a_hand_named_board_is_known_to_the_pi_pairing_gate():
    b = src("ui/screens/birth_guide_screen.py")
    branch = b[b.index('self._board_source = "operator"'):b.index('self._board_source = "operator"') + 500]
    assert "self._board_preknown = True" in branch


def test_two_boards_on_the_reading_step_are_named_not_read_at_random():
    b = src("ui/screens/birth_guide_screen.py")
    work = b[b.index("ports = local_board_ports()\n                if len(ports) > 1:"):]
    assert "raise _ManyBoards()" in work[:200]                  # nothing is read
    named = b[b.index("except _ManyBoards:"):b.index("except Exception as e:      # noqa: BLE001\n                c = {\"kind\": \"birth\", \"reason\": f\"Couldn't read the board: {e}\"}")]
    assert "Two boards are plugged in — unplug one" in named     # and it is said


def test_the_phone_handoff_button_stays_live_until_a_url_exists():
    c = src("ui/screens/comms_screen.py")
    send = c[c.index("    def _send(self, app, card):"):c.index("card.add_widget(panel)")]
    assert send.count("card._qr_open = True") == 1
    assert send.index("card._qr_open = True") > send.index("else:")      # only with a URL
    assert "card._notice = panel" in send
    assert "Get the medic and the phone on the SAME Wi-Fi first" not in c
    assert "Turn on your phone's hotspot, connect" in c


def test_no_screen_promises_a_card_check_that_does_not_exist():
    assert "Its card can be checked too" not in src("ui/screens/birth_screen.py")


def test_a_finished_handoff_never_yanks_the_operator_off_another_screen():
    a = src("ui/app.py")
    body = a[a.index("def resume_guided_birth"):a.index("def guided_birth_pending")]
    assert 'not in ("birth", "pi_imager")' in body and "return False" in body


def test_chat_says_how_to_delete_and_the_doc_names_the_real_button():
    assert 'tr("Hold a conversation to delete it")' in src("ui/screens/chat_screen.py")
    d = src("docs/CHAT.md")
    assert '"Phone apps", top right' not in d and "Put Columba or Sideband on a phone" in d
    assert "hold its row in the list" in d


def test_outgoing_messages_keep_their_lxmf_hash(tmp_path):
    from monitor.lxmf_chat import MessageStore
    store = MessageStore(str(tmp_path / "chat"))
    rec = store.add_outgoing("c" * 32, "quote me")
    assert store.set_lxmf_hash(rec["id"], "ab" * 16) is True
    assert store.text_of("ab" * 16) == "quote me"              # found by the LXMF hash
    assert store.text_of(rec["id"]) == "quote me"              # and still by its own id
    assert store.set_lxmf_hash(rec["id"], "ab" * 16) is False  # idempotent
    s = src("monitor/chat_service.py")
    assert "self.store.set_lxmf_hash(msg_id, bytes(h).hex())" in s
    # SENDING is written before the hand-off, never after a callback may have fired
    disp = s[s.index("def _dispatch"):]
    assert disp.index("store_mod.SENDING)") < disp.index("self._router.handle_outbound(lxm)")


def test_clone_buttons_and_the_language_toast():
    m = src("ui/screens/mitosis_screen.py")
    assert 'self.pw_btn.text = tr("Next →")' in m
    wifi = m[m.index("def _show_stage_wifi"):m.index("def _wifi_continue")]
    assert 'tr("← Back")' in wifi and "self._show_stage_password()" in wifi
    l = src("ui/screens/language_screen.py")
    assert "p.dismiss(), 3.5)" not in l


def test_probe_refusals_are_translated():
    h = src("ui/hw_factories.py")
    assert 'tr("Which board?")' in h and 'tr("No board to PROBE")' in h
    assert "from ui.i18n import tr" in h
    for code in SHIPPED:
        with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"), encoding="utf-8") as f:
            cat = json.load(f)
        for k in ("Which board?", "No board to PROBE", "Hold a conversation to delete it",
                  "Two boards are plugged in — unplug one and read again."):
            assert k in cat, (code, k)
