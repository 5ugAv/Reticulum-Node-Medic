"""A way to say no on Settings ▸ Trusted operators (operator, 2026-09-21:
revoking four dead test clones left them listed as UNTRUSTED with only an
Approve button). Forgetting removes the record; it never trusts."""
from monitor import trust


def _store(tmp_path):
    p = str(tmp_path / "trust.json")
    trust.set_self("aa" * 16, "medic", path=p)
    trust.record_child_clone("bb" * 16, "Clone bb", "aa" * 16, path=p)
    return p


def test_forget_removes_a_unit_and_never_the_self_unit(tmp_path):
    p = _store(tmp_path)
    assert trust.classify("bb" * 16, path=p) == "trusted"
    trust.forget("bb" * 16, path=p)
    assert trust.classify("bb" * 16, path=p) == "unknown"
    assert trust.is_trusted("bb" * 16, path=p) is False
    trust.forget("aa" * 16, path=p)
    assert trust.classify("aa" * 16, path=p) == "self"
    trust.forget("cc" * 16, path=p)                     # unknown: a no-op
    assert [u["hash"] for u in trust.units(path=p)] == ["aa" * 16]


def test_revoke_is_remembered_so_the_screen_can_say_so(tmp_path):
    p = _store(tmp_path)
    trust.revoke("bb" * 16, path=p)
    u = [x for x in trust.units(path=p) if x["hash"] == "bb" * 16][0]
    assert u["status"] == "untrusted" and u["revoked"] is True


def test_the_screen_offers_forget_beside_approve():
    src = open("ui/screens/trusted_operators_screen.py").read()
    assert "Forget this unit" in src and "_confirm_forget" in src
    assert "trust.forget(" in src
    assert "trust revoked" in src
