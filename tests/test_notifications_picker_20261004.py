"""Settings > Notifications: the operator's own address can be PICKED from
what CHAT already knows, not only typed (operator, 2026-10-04: "reference any
addresses that have already been added to the node medic under chat").
"""
import json
import os

import pytest

from monitor.lxmf_chat import MessageStore
from tests.srcutil import func_source, src

PHONE = "a" * 32
FRIEND = "b" * 32
HEARD = "c" * 32
ME = "d" * 32
LANGS = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")
NEW_KEYS = (
    "Or pick an address Chat already knows:",
    "Chat knows no addresses yet — message your phone from CHAT once, or type the address above.",
    "Filled in from Chat — tap Save to use this address.",
)


@pytest.fixture
def store(tmp_path):
    return MessageStore(str(tmp_path / "chat"))


def test_contacts_lists_conversations_first_then_heard_peers(store):
    store.remember_peer(HEARD, name="Rooftop relay", seen=10.0)
    store.add_incoming(FRIEND, "hello", ts=100.0, msg_id="m1")
    store.add_outgoing(PHONE, "to myself", ts=200.0)
    store.remember_peer(PHONE, name="My phone", seen=200.0)
    rows = store.contacts()
    assert [r["hash"] for r in rows] == [PHONE, FRIEND, HEARD]
    assert rows[0]["name"] == "My phone"
    assert rows[1]["name"] == FRIEND[:8], "no announced name -> the short hash"
    assert rows[2]["name"] == "Rooftop relay"


def test_contacts_excludes_the_medics_own_address_and_honours_the_limit(store):
    store.add_outgoing(ME, "note to self", ts=300.0)
    store.add_incoming(FRIEND, "hi", ts=100.0, msg_id="m2")
    store.remember_peer(HEARD, seen=1.0)
    assert [r["hash"] for r in store.contacts(exclude=(ME,))] == [FRIEND, HEARD]
    assert len(store.contacts(limit=1, exclude=(ME,))) == 1


def test_an_empty_store_offers_nothing_without_raising(store):
    assert store.contacts() == []


# -- the screen and its wiring (source pins; Kivy is not importable here) -----

def test_the_screen_builds_the_picker_on_enter_and_fills_the_field():
    s = src("ui/screens/notifications_screen.py")
    assert "def __init__(self, contacts=None, **kwargs):" in s
    enter = func_source("ui/screens/notifications_screen.py", "enter",
                        cls="NotificationsScreen")
    assert "self._contacts()" in enter and "self._picks.clear_widgets()" in enter
    # the empty note must not keep Kivy's default 100 px (a blank band above the list)
    assert "self._pick_note.height = 0" in enter
    assert s.count("self._pick_note.height = 0") == 2
    assert "Chat knows no addresses yet" in enter
    pick = func_source("ui/screens/notifications_screen.py", "_pick",
                       cls="NotificationsScreen")
    assert "self.field.text = addr" in pick and "tap Save" in pick


def test_the_app_hands_chat_contacts_over_lazily_and_refreshes_on_enter():
    app = src("ui/app.py")
    assert "contacts=lambda: self._chat_contacts()" in app
    assert "notif_scr.bind(on_enter=lambda *_: self.notifications_screen.enter())" in app
    body = func_source("ui/app.py", "_chat_contacts", cls="ReticulumNodeMedicApp")
    assert "store.contacts(exclude=(own,))" in body
    assert 'getattr(self, "chat_store", None)' in body


def test_the_picker_strings_are_translated_in_every_language():
    for code in LANGS:
        d = json.load(open(os.path.join("assets", "i18n", code + ".json"),
                           encoding="utf-8"))
        for key in NEW_KEYS:
            assert d.get(key) and d[key] != key, (code, key)
