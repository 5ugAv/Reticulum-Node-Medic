"""On-medic birth-certificate store — persist, search, notes."""

from ui.cert_store import (save_cert, load_certs, search_certs, update_notes,
                           cert_id)


def _cert(name, sid, **extra):
    return dict({"node_name": name, "session_id": sid,
                 "hostname": f"{name.lower()}.local"}, **extra)


def test_save_and_load_roundtrip(tmp_path):
    d = str(tmp_path)
    cid = save_cert(_cert("Rooftop-East", "20260721_1"), d, now=100)
    certs = load_certs(d)
    assert len(certs) == 1
    assert certs[0]["node_name"] == "Rooftop-East"
    assert certs[0]["_id"] == cid


def test_resave_same_node_overwrites_not_duplicates(tmp_path):
    d = str(tmp_path)
    save_cert(_cert("Tower", "20260721_9"), d, now=1)
    save_cert(_cert("Tower", "20260721_9", location="-37.7, 145.0 (map)"), d, now=2)
    certs = load_certs(d)
    assert len(certs) == 1                     # stable id -> one file
    assert certs[0]["location"] == "-37.7, 145.0 (map)"


def test_newest_first(tmp_path):
    d = str(tmp_path)
    save_cert(_cert("A", "a"), d, now=10)
    save_cert(_cert("B", "b"), d, now=20)
    assert [c["node_name"] for c in load_certs(d)] == ["B", "A"]


def test_search_by_name_case_insensitive(tmp_path):
    d = str(tmp_path)
    save_cert(_cert("Rooftop-East", "1"), d, now=1)
    save_cert(_cert("Garden-Shed", "2"), d, now=2)
    hits = search_certs("rooftop", d)
    assert len(hits) == 1 and hits[0]["node_name"] == "Rooftop-East"
    assert len(search_certs("", d)) == 2       # blank -> browse all


def test_update_notes_persists(tmp_path):
    d = str(tmp_path)
    cid = save_cert(_cert("East", "1"), d, now=1)
    assert update_notes(cid, "4m mast on the water tank", d) is True
    assert load_certs(d)[0]["notes"] == "4m mast on the water tank"
    assert update_notes("nope", "x", d) is False


def test_cert_id_stable_and_slugged():
    a = cert_id({"node_name": "Rooftop East!", "session_id": "20260721_1"})
    b = cert_id({"node_name": "Rooftop East!", "session_id": "20260721_1"})
    assert a == b == "rooftop-east-20260721-1"


def test_load_missing_dir_is_empty(tmp_path):
    assert load_certs(str(tmp_path / "nope")) == []


def test_delete_by_name_frees_the_name_for_a_new_birth(tmp_path):
    """The operator's delete-node action (2026-08-13): every record of the
    name goes, so a new birth under it starts clean. Case-insensitive — the
    registry shows TTT for a node born ttt."""
    from ui.cert_store import delete_by_name
    d = str(tmp_path)
    save_cert({"node_type": "pi", "node_name": "ttt", "session_id": "a"}, d, now=1)
    save_cert({"node_type": "rnode", "node_name": "TTT", "session_id": "b"}, d, now=2)
    save_cert({"node_type": "pi", "node_name": "HOPE", "session_id": "c"}, d, now=3)
    assert delete_by_name("ttt", d) == 2
    left = load_certs(d)
    assert len(left) == 1 and left[0]["node_name"] == "HOPE"
    assert delete_by_name("nonesuch", d) == 0
