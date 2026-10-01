"""The chat store: what the screen reads and the service writes (docs/CHAT.md)."""
import json
import os

import pytest

from monitor import lxmf_chat as lc
from monitor.lxmf_chat import MessageStore

PEER = "a" * 32
OTHER = "b" * 32


@pytest.fixture
def store(tmp_path):
    return MessageStore(str(tmp_path / "chat"))


def test_incoming_is_unread_and_bumps_version(store):
    v0 = store.version
    rec = store.add_incoming(PEER, "hello", ts=100.0, msg_id="m1")
    assert rec["dir"] == lc.IN and rec["read"] is False and rec["state"] == lc.DELIVERED
    assert store.version > v0
    assert store.unread_total() == 1


def test_same_lxmf_message_is_stored_once(store):
    assert store.add_incoming(PEER, "hello", ts=100.0, msg_id="m1") is not None
    assert store.add_incoming(PEER, "hello", ts=100.0, msg_id="m1") is None
    assert len(store.thread(PEER)) == 1


def test_outgoing_walks_its_states(store):
    rec = store.add_outgoing(PEER, "hi", ts=50.0)
    assert rec["state"] == lc.SENDING and rec["read"] is True
    assert store.set_state(rec["id"], lc.DELIVERED)
    assert store.thread(PEER)[0]["state"] == lc.DELIVERED
    assert not store.set_state(rec["id"], lc.DELIVERED)     # no change, no write
    with pytest.raises(ValueError):
        store.set_state(rec["id"], "teleported")


def test_conversations_are_newest_first_with_unread_counts(store):
    store.add_incoming(PEER, "old", ts=10.0, msg_id="1")
    store.add_incoming(OTHER, "newer", ts=20.0, msg_id="2")
    store.add_incoming(PEER, "newest", ts=30.0, msg_id="3")
    convs = store.conversations()
    assert [c.peer for c in convs] == [PEER, OTHER]
    assert convs[0].last_text == "newest" and convs[0].unread == 2
    assert convs[1].unread == 1
    assert store.mark_read(PEER) == 2
    assert store.conversations()[0].unread == 0


def test_peer_names_come_from_announces_else_short_hash(store):
    assert store.peer_name(PEER) == "aaaaaaaa"
    store.remember_peer(PEER, name="Marnie's phone", seen=5.0)
    assert store.peer_name(PEER) == "Marnie's phone"
    assert store.conversations() == []
    store.add_incoming(PEER, "x", ts=1.0, msg_id="1")
    assert store.conversations()[0].name == "Marnie's phone"
    store.remember_peer(OTHER, name="", seen=9.0)
    assert [p["hash"] for p in store.peers()] == [OTHER, PEER]


def test_everything_survives_a_reload(store, tmp_path):
    store.add_incoming(PEER, "kept", ts=1.0, msg_id="1")
    store.remember_peer(PEER, name="Kept name")
    again = MessageStore(str(tmp_path / "chat"))
    assert again.thread(PEER)[0]["text"] == "kept"
    assert again.peer_name(PEER) == "Kept name"
    # private to the medic's own user
    assert oct(os.stat(tmp_path / "chat" / "messages.json").st_mode & 0o777) == "0o600"


def test_a_corrupt_file_is_an_empty_store_not_a_crash(tmp_path):
    d = tmp_path / "chat"; d.mkdir()
    (d / "messages.json").write_text("{not json")
    (d / "peers.json").write_text(json.dumps([1, 2]))
    s = MessageStore(str(d))
    assert s.conversations() == [] and s.peers() == []


def test_a_write_from_another_process_is_seen(tmp_path):
    """The app's store and a shell's store on the same directory: the app
    must notice what the shell wrote (2026-09-30: a planted message never
    showed because the running store never re-read the file)."""
    import os, time
    app = MessageStore(str(tmp_path / "chat"))
    assert app.conversations() == []                  # loaded, empty
    v = app.version
    shell = MessageStore(str(tmp_path / "chat"))
    shell.remember_peer(PEER, name="Shell")
    shell.add_incoming(PEER, "planted", ts=1.0, msg_id="p1")
    # make the mtime unambiguously newer on coarse filesystems
    f = tmp_path / "chat" / "messages.json"
    os.utime(f, (time.time() + 2, time.time() + 2))
    assert app.unread_total() == 1
    assert app.conversations()[0].name == "Shell"
    assert app.version > v                             # the screen gets told


def test_poll_notices_a_foreign_write_before_any_read(tmp_path):
    import os, time
    app = MessageStore(str(tmp_path / "chat"))
    v0 = app.poll()                                    # loads (empty)
    shell = MessageStore(str(tmp_path / "chat"))
    shell.add_incoming(PEER, "planted", ts=1.0, msg_id="p1")
    f = tmp_path / "chat" / "messages.json"
    os.utime(f, (time.time() + 2, time.time() + 2))
    assert app.poll() > v0                             # no read in between
    assert app.unread_total() == 1


def test_preview_is_one_line_cut_on_a_word():
    from monitor.lxmf_chat import preview as _preview
    assert _preview("short") == "short"
    assert _preview("line one\nline two") == "line one line two"
    long = "Test message planted by the walkthrough — proves the badge and the thread."
    p = _preview(long)
    assert p.endswith("…") and len(p) <= 45 and not p[:-1].endswith(" ")
    assert p == "Test message planted by the walkthrough —…"


def test_a_deleted_file_is_noticed_too(tmp_path):
    """The planted test peer stayed on the screen after its files were
    deleted (2026-10-01): a missing file has mtime 0, which was 'not newer'."""
    import os
    s = MessageStore(str(tmp_path / "chat"))
    s.remember_peer(PEER, name="Gone soon")
    s.add_incoming(PEER, "x", ts=1.0, msg_id="1")
    assert s.peer_name(PEER) == "Gone soon"
    os.remove(tmp_path / "chat" / "messages.json")
    os.remove(tmp_path / "chat" / "peers.json")
    assert s.conversations() == [] and s.peers() == []


def test_a_typed_address_is_not_called_heard():
    src = open("ui/screens/chat_screen.py").read()
    i = src.index("def _open_typed")
    assert "remember_peer" not in src[i:]
    assert "self._store.mark_read(self._peer)" in src[src.index("def _render_thread"):src.index("def _route_line")]


def test_delete_conversation_removes_the_messages_and_keeps_the_peer(store):
    store.remember_peer(PEER, name="Marnie")
    store.add_incoming(PEER, "one", ts=1.0, msg_id="1")
    store.add_outgoing(PEER, "two", ts=2.0)
    store.add_incoming(OTHER, "other", ts=3.0, msg_id="3")
    v = store.version
    assert store.delete_conversation(PEER) == 2
    assert store.version > v
    assert store.thread(PEER) == [] and [c.peer for c in store.conversations()] == [OTHER]
    assert store.peer_name(PEER) == "Marnie"          # still known on the mesh
    assert store.delete_conversation(PEER) == 0       # nothing to do, no write


def test_press_and_hold_on_a_row_offers_delete():
    src = open("ui/screens/chat_screen.py").read()
    assert "class _HoldRow(Button)" in src and "HOLD_S = 0.6" in src
    assert 'leave_text=tr("Delete"), leave_color="red"' in src
    assert "self._store.delete_conversation(peer)" in src
    # a hold is never also a tap
    body = src[src.index("def on_touch_up"):src.index("def _fire")]
    assert "return True" in body


def test_emoji_become_words_the_medics_fonts_can_draw():
    from monitor.lxmf_chat import readable, describe_fields
    assert readable("👍") == "[thumbs up sign]"
    assert readable("ok 👍🏽 then") == "ok [thumbs up sign][emoji modifier fitzpatrick type-4] then"
    assert readable("plain text") == "plain text"
    assert readable("❤️") == "[heavy black heart]"           # FE0F dropped
    assert describe_fields({}, "") == "(an empty message)"
    assert describe_fields({}, "👍") == "[thumbs up sign]"
    assert describe_fields({0x06: b"\x89PNG" + b"\x00" * 200}) == "(image)"
    assert describe_fields({0x77: b"\xf0\x9f\x91\x8d"}) == "([thumbs up sign])"
    assert describe_fields({0x77: "x" * 100}) == "(field 119)"


def test_a_columba_reaction_names_the_emoji_and_the_message_it_was_on():
    """FIELD_REACTION 0x40 = {0x00: target hash, 0x01: utf-8 emoji} — what
    the phone sent at 23:54 on 2026-10-01, drawn as an empty bubble."""
    from monitor.lxmf_chat import describe_fields
    target = bytes.fromhex("ab" * 32)
    f = {0x40: {0x00: target, 0x01: "👍".encode()}}
    assert describe_fields(f, text_of=lambda h: "testinf in home mode" if h == "ab" * 32 else None) \
        == "([thumbs up sign] to \u201ctestinf in home mode\u201d)"
    assert describe_fields(f, text_of=lambda h: None) == "([thumbs up sign] to an earlier message)"
    assert describe_fields({0x30: target, 0x31: b"quoted"}) == "(an empty message)"


def test_store_text_of(store):
    store.add_outgoing(PEER, "hello", msg_id="ab" * 32)
    assert store.text_of("ab" * 32) == "hello" and store.text_of("ff" * 32) is None
