"""Rebirth retires its predecessor's identities — from the CERTIFICATES.

The roster-based retire paths go blind when the roster is empty (wiped
2026-08-22), while the prior certs still record exactly which hashes a name
used to be. 2026-08-25, live: three of old-ELSEWHERE's identities sat on
VITALS as anonymous ghost neighbours through a full rebirth. These pin the
cert-scan helper the birth flow now calls.
"""

import json
import os

from ui.cert_store import predecessor_hashes


def _cert(dirp, fn, **fields):
    with open(os.path.join(dirp, fn), "w") as f:
        json.dump(fields, f)


def test_prior_certs_same_name_yield_their_hashes(tmp_path):
    d = str(tmp_path)
    _cert(d, "elsewhere.json", node_name="ELSEWHERE",
          reticulum_address="oldaaa", health_dst="oldbbb", lxmd_dst="oldccc")
    got = predecessor_hashes("ELSEWHERE", ["newaaa"], cert_dir=d)
    assert got == {"oldaaa", "oldbbb", "oldccc"}


def test_matching_is_case_insensitive_and_by_hostname_too(tmp_path):
    d = str(tmp_path)
    _cert(d, "old.json", hostname="elsewhere", reticulum_address="oldaaa")
    assert predecessor_hashes("ELSEWHERE", [], cert_dir=d) == {"oldaaa"}


def test_the_new_certs_own_hashes_are_never_retired(tmp_path):
    d = str(tmp_path)
    _cert(d, "elsewhere-new.json", node_name="ELSEWHERE",
          reticulum_address="keep1", health_dst="keep2", lxmd_dst="old3")
    got = predecessor_hashes("ELSEWHERE", ["keep1", "keep2"], cert_dir=d)
    assert got == {"old3"}              # re-issuing for the SAME machine: safe


def test_other_names_and_corrupt_certs_are_ignored(tmp_path):
    d = str(tmp_path)
    _cert(d, "skyfinger.json", node_name="SKYFINGER", reticulum_address="sky1")
    open(os.path.join(d, "broken.json"), "w").write("not json{")
    open(os.path.join(d, "notes.txt"), "w").write("ignored")
    assert predecessor_hashes("ELSEWHERE", [], cert_dir=d) == set()


def test_missing_dir_and_blank_name_are_empty_not_a_raise(tmp_path):
    assert predecessor_hashes("X", [], cert_dir=str(tmp_path / "nope")) == set()
    assert predecessor_hashes("", [], cert_dir=str(tmp_path)) == set()
