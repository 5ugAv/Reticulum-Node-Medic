"""A message sent to someone out of reach says WHERE it is waiting.

2026-10-04: the bubble said "held at the post office", which tells a keeper
nothing they can act on. It read "waiting at propagation node (<name>)" for an
afternoon; the name was always this medic's own (its lxmd is the propagation
node every no-path send is handed to), so the operator asked for the plainest
true words: "held here until they're back online". The holding node's name is
still kept on the record (``via``) for the day there is more than one.
"""
import ast
import json
import os

import pytest

from monitor import lxmf_chat as lc
from monitor.lxmf_chat import MessageStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODES = ["es", "fr", "de", "ja", "ru", "pl", "id", "sv"]
PEER = "d" * 32

NEW_STATE = "held here until they're back online"
NEW_ROUTE = "no path right now — held here until they're back online"
OLD = ("held at the post office",
       "no path right now — messages wait at the post office",
       "waiting at propagation node",
       "waiting at propagation node ({name})",
       "no path right now — messages wait at the propagation node",
       "waiting at this medic's propagation node",
       "no path right now — messages wait at this medic's propagation node")


def _catalog(code):
    with open(os.path.join(ROOT, "assets", "i18n", code + ".json"),
              encoding="utf-8") as f:
        return json.load(f)


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def _state_words_fn(tr):
    """chat_screen.py can't be imported without Kivy, so lift the one function
    out of the source and run it against the real state words."""
    tree = ast.parse(_src("ui/screens/chat_screen.py"))
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_state_words")
    ns = {"lc": lc, "tr": tr}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "chat_screen", "exec"), ns)
    return ns["_state_words"]


# ---- the store ----

def test_the_holding_node_is_kept_on_the_record_and_survives_a_restart(tmp_path):
    store = MessageStore(str(tmp_path / "chat"))
    rec = store.add_outgoing(PEER, "hold this")
    assert store.set_state(rec["id"], lc.POSTED, via="Bench medic") is True
    assert MessageStore(str(tmp_path / "chat")).thread(PEER)[0]["via"] == "Bench medic"


def test_set_state_still_reports_whether_anything_changed(tmp_path):
    store = MessageStore(str(tmp_path / "chat"))
    rec = store.add_outgoing(PEER, "x")
    assert store.set_state(rec["id"], lc.SENT) is True
    assert store.set_state(rec["id"], lc.SENT) is False            # same again: no write
    assert store.set_state(rec["id"], lc.POSTED, via="A") is True
    assert store.set_state(rec["id"], lc.POSTED, via="A") is False
    assert store.set_state(rec["id"], lc.POSTED, via="B") is True  # a different holder
    assert store.thread(PEER)[0]["via"] == "B"
    assert store.set_state("no-such-id", lc.POSTED, via="A") is False
    with pytest.raises(ValueError):
        store.set_state(rec["id"], "bogus")


def test_a_state_change_without_a_node_name_leaves_no_via(tmp_path):
    store = MessageStore(str(tmp_path / "chat"))
    rec = store.add_outgoing(PEER, "x")
    store.set_state(rec["id"], lc.DELIVERED)
    assert "via" not in store.thread(PEER)[0]


# ---- the words ----

def test_the_state_words_say_propagation_node_not_post_office():
    assert lc.STATE_WORDS[lc.POSTED] == NEW_STATE
    for words in lc.STATE_WORDS.values():
        assert "post office" not in words


def test_a_waiting_message_says_held_here_named_or_not():
    """The name on the record is this medic's own, every time — so the words
    say "held here" and leave the name out (operator, 2026-10-04)."""
    words = _state_words_fn(lambda s: s)
    assert words({"state": lc.POSTED, "via": "Bench medic"}) == NEW_STATE
    assert words({"state": lc.POSTED}) == NEW_STATE
    assert words({"state": lc.POSTED, "via": ""}) == NEW_STATE
    assert words({"state": lc.POSTED, "via": "a{b}c"}) == NEW_STATE


def test_every_other_state_reads_as_before():
    words = _state_words_fn(lambda s: s)
    for state in (lc.SENDING, lc.SENT, lc.DELIVERED, lc.FAILED):
        assert words({"state": state, "via": "ignored"}) == lc.STATE_WORDS[state]


def test_the_waiting_words_translate(tmp_path):
    es = _catalog("es")
    words = _state_words_fn(lambda s: es.get(s, s))
    got = words({"state": lc.POSTED, "via": "Bench medic"})
    assert got == "retenido aquí hasta que vuelvan a estar en línea"


# ---- the screen ----

def test_the_screen_builds_the_bubble_words_through_the_helper():
    s = _src("ui/screens/chat_screen.py")
    assert 'meta += "  ·  " + _state_words(rec)' in s
    assert "waiting at propagation node ({name})" not in s
    assert 'tr("' + NEW_ROUTE + '")' in s


def test_the_service_hands_its_own_name_with_the_posted_state():
    s = _src("monitor/chat_service.py")
    assert "store_mod.POSTED, via=self.display_name" in s


# ---- the catalogs ----

@pytest.mark.parametrize("code", CODES)
def test_every_language_has_the_new_words_and_none_of_the_old(code):
    d = _catalog(code)
    for key in (NEW_STATE, NEW_ROUTE):
        assert d.get(key), (code, key)
        assert d[key] != key, (code, key)                  # translated, not echoed
    for old in OLD:
        assert old not in d, (code, old)


@pytest.mark.parametrize("code", CODES)
def test_every_state_word_is_in_every_catalog(code):
    d = _catalog(code)
    assert [w for w in lc.STATE_WORDS.values() if w not in d] == []
