"""Trust-store integrity — keyed-HMAC tamper detection (audit C8).

Two layers are covered: the pure ``trust_integrity`` sign/verify/key helpers
(with an injected key + tmp paths, never the real home), and the wiring into
``monitor.trust`` load/save (transparent signing, tamper rejection, and the
one-time migration of a legacy store that has no sidecar yet).
"""

import inspect
import json
import os
import stat

from monitor import trust, trust_integrity


# --- pure sign / verify -----------------------------------------------------

def test_sign_verify_round_trip():
    key = b"k" * 32
    data = trust_integrity.canonical_bytes({"units": {"aaaa": {"trusted": True}}})
    sig = trust_integrity.sign(key, data)
    assert trust_integrity.verify(key, data, sig) is True


def test_verify_rejects_tampered_data():
    key = b"k" * 32
    good = trust_integrity.canonical_bytes({"units": {"aaaa": {"trusted": False}}})
    sig = trust_integrity.sign(key, good)
    forged = trust_integrity.canonical_bytes({"units": {"aaaa": {"trusted": True}}})
    assert trust_integrity.verify(key, forged, sig) is False


def test_verify_rejects_wrong_key():
    data = trust_integrity.canonical_bytes({"units": {}})
    sig = trust_integrity.sign(b"a" * 32, data)
    assert trust_integrity.verify(b"b" * 32, data, sig) is False


def test_canonical_bytes_is_order_invariant():
    a = trust_integrity.canonical_bytes({"x": 1, "y": 2})
    b = trust_integrity.canonical_bytes({"y": 2, "x": 1})
    assert a == b


def test_verify_uses_compare_digest_not_equality():
    src = inspect.getsource(trust_integrity.verify)
    assert "compare_digest" in src
    assert "==" not in src            # no timing-leaky plain comparison


# --- key file ---------------------------------------------------------------

def test_key_created_0600(tmp_path):
    kp = str(tmp_path / "sub" / "trust_hmac_key")
    key = trust_integrity.load_or_create_key(kp)
    assert len(key) == trust_integrity.KEY_SIZE
    mode = stat.S_IMODE(os.stat(kp).st_mode)
    assert mode == 0o600
    # stable: a second call returns the SAME key (loads, not regenerates)
    assert trust_integrity.load_or_create_key(kp) == key


# --- trust.py wiring --------------------------------------------------------

def _p(tmp_path):
    return str(tmp_path / "trust.json")


def test_save_writes_sidecar_and_load_verifies(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "My Medic", now=1.0, path=p)
    assert os.path.exists(p + ".sig")
    # a clean round-trip still trusts the self unit
    assert trust.is_trusted("aaaa", path=p) is True


def test_tampered_store_is_ignored(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "Origin", now=1.0, path=p)
    trust.record_child_clone("bbbb", "Friend", parent_hash="aaaa", now=2.0, path=p)
    assert trust.is_trusted("bbbb", path=p) is True

    # An attacker rewrites the JSON to trust a brand-new stranger unit, but can't
    # forge the HMAC (no key). The sidecar no longer matches the file.
    store = json.load(open(p))
    store["units"]["zzzz"] = {"name": "Stranger", "trusted": True}
    with open(p, "w") as f:
        json.dump(store, f)

    # The whole store is rejected -> everything reads untrusted, incl. the
    # previously-legitimate entries (fail safe, not fail open).
    assert trust.is_trusted("zzzz", path=p) is False
    assert trust.is_trusted("bbbb", path=p) is False
    assert trust.load(path=p) == {"units": {}}


def test_legacy_store_migrates_once_then_verifies(tmp_path):
    p = _p(tmp_path)
    # A pre-integrity store written by old code: JSON present, NO sidecar.
    legacy = {"units": {"aaaa": {"name": "Origin", "trusted": True, "self": True}}}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        json.dump(legacy, f, indent=2, sort_keys=True)
    assert not os.path.exists(p + ".sig")

    # First load accepts it AND stamps a fresh signature (migration).
    assert trust.is_trusted("aaaa", path=p) is True
    assert os.path.exists(p + ".sig")

    # Subsequent loads verify against that fresh signature (no re-migration path).
    assert trust.is_trusted("aaaa", path=p) is True
    assert trust.classify("aaaa", path=p) == "self"


def test_key_lands_beside_the_store(tmp_path):
    p = _p(tmp_path)
    trust.set_self("aaaa", "Medic", path=p)
    assert os.path.exists(str(tmp_path / "trust_hmac_key"))
