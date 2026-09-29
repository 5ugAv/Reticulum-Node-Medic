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
