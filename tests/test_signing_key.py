"""A medic names boards with its OWN signing key; a clone has to make one
first (Node Medic 2 stopped at "No signing key found", 2026-10-06), and it
trusts its parent's public key so the parent's boards verify on it."""
import hashlib

from workflows import signing_key as sk


class _Conn:
    def __init__(self, have_key=False):
        self.have_key = have_key; self.cmds = []
    def run(self, cmd, timeout=None):
        self.cmds.append(cmd)
        if cmd.startswith("test -f"):
            return (0 if self.have_key else 1), "", ""
        if "rnodeconf -k" in cmd:
            self.have_key = True
            return 0, "Generating a new EEPROM signing key...", ""
        return 0, "", ""


def test_a_medic_with_a_key_is_left_alone():
    c = _Conn(have_key=True)
    assert sk.ensure_signing_key(c) == (True, "")
    assert not any("rnodeconf -k" in x for x in c.cmds)


def test_a_fresh_clone_makes_its_own_key_once():
    c = _Conn(have_key=False)
    ok, note = sk.ensure_signing_key(c)
    assert ok and "first time only" in note
    assert sum("rnodeconf -k" in x for x in c.cmds) == 1


def test_a_key_that_still_is_not_there_is_an_honest_failure():
    class _Stuck(_Conn):
        def run(self, cmd, timeout=None):
            if "rnodeconf -k" in cmd:
                return 81, "", "Please ensure filesystem access and try again."
            return super().run(cmd, timeout)
    ok, note = sk.ensure_signing_key(_Stuck())
    assert not ok and "cannot name a board yet" in note and "filesystem access" in note


def test_public_key_file_matches_what_trust_key_would_write(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    priv = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    p = tmp_path / "signing.key"
    p.write_bytes(priv.private_bytes(serialization.Encoding.DER,
                                     serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()))
    name, pub = sk.public_key_file(str(p))
    want = priv.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    assert pub == want and name == hashlib.sha256(want).hexdigest() + ".pubkey"
    assert sk.public_key_file(str(tmp_path / "none.key")) is None


def test_every_flash_makes_the_key_before_naming_anything():
    from tests.srcutil import func_source
    body = func_source("workflows/rnode_flash.py", "_flash")
    assert "ensure_signing_key(" in body


def test_the_clone_hands_its_child_the_parents_public_key():
    from tests.srcutil import func_source
    body = func_source("workflows/clone.py", "record_child_trust")
    assert "public_key_file(" in body and "trusted_keys" in body
