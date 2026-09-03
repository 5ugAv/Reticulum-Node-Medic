"""Per-file encryption of the medic's records.

The LUKS path in ``provisioning/vault.py`` cannot ship: cryptsetup is not
installed on the medic and is not in assets/packages, so an offline clone could
never enable it. These tests cover the replacement, and in particular the
KEYSLOT model that vault.py documented but never built — ``luksAddKey`` appears
nowhere in the repo, so the recovery key the operator is forced to write down
and type back opened nothing at all.
"""
import json
import os

import pytest

from provisioning.records_vault import (
    KEYRING_NAME, MAGIC, SLOT_DAILY, SLOT_PASSPHRASE, SLOT_RECOVERY,
    Keyring, VaultError, decrypt_bytes, decrypt_tree, encrypt_bytes,
    encrypt_tree, is_encrypted, is_vault, load_keyring, new_data_key,
    save_keyring, unwrap_data_key, wrap_data_key,
)
from provisioning.vault import ScryptParams

#: The vault needs a real AEAD, which comes from ``cryptography``. It is
#: installed on the medic and carried in the wheelhouse, and CI installs it too
#: — but a machine without it should SKIP these, not fail them. The module's own
#: behaviour there is correct and separately tested: it raises a sentence the
#: operator can act on, and ``blockers()`` reports it.
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
    _HAVE_CRYPTO = True
except Exception:                                          # pragma: no cover
    _HAVE_CRYPTO = False

pytestmark = pytest.mark.skipif(
    not _HAVE_CRYPTO,
    reason="cryptography is not installed; the vault cannot be exercised here")


#: scrypt at the shipped N=2**17 costs ~128 MiB and about a second PER CALL.
#: The tests below make hundreds of calls; the KDF is vault.py's, tested there.
FAST = ScryptParams(n=1 << 8, r=8, p=1)


def _ring(dk, **secrets_by_name):
    r = Keyring()
    for name, secret in secrets_by_name.items():
        r.add(dk, secret, name, FAST)
    return r


# --------------------------------------------------------------------------- #
# Slots
# --------------------------------------------------------------------------- #

def test_a_wrapped_key_comes_back_out():
    dk = new_data_key()
    slot = wrap_data_key(dk, "correct horse battery", SLOT_PASSPHRASE, FAST)
    assert unwrap_data_key(slot, "correct horse battery") == dk


def test_the_wrong_secret_returns_none_rather_than_raising():
    """A wrong passphrase is the ordinary case at an unlock screen. It must not
    be distinguishable from a corrupt slot — that difference is what a probe
    would want."""
    slot = wrap_data_key(new_data_key(), "right", SLOT_PASSPHRASE, FAST)
    assert unwrap_data_key(slot, "wrong") is None
    assert unwrap_data_key(slot, "") is None


def test_the_wrapping_never_contains_the_data_key():
    dk = new_data_key()
    slot = wrap_data_key(dk, "s3cret passphrase", SLOT_PASSPHRASE, FAST)
    blob = json.dumps(slot.to_json()).encode()
    assert dk not in slot.wrapped
    assert dk not in blob


def test_an_empty_secret_is_refused_at_wrap_time():
    """Wrapping with "" would install a door anyone can walk through, and the
    keyring would look completely normal."""
    with pytest.raises(VaultError):
        wrap_data_key(new_data_key(), "", SLOT_PASSPHRASE, FAST)


def test_the_slot_name_is_authenticated():
    """A keyring is a plain file. Relabelling the recovery slot as the daily one
    would present a 160-bit door as the weak one the operator chose."""
    dk = new_data_key()
    slot = wrap_data_key(dk, "pass phrase here", SLOT_RECOVERY, FAST)
    renamed = type(slot)(name=SLOT_DAILY, salt=slot.salt, nonce=slot.nonce,
                         wrapped=slot.wrapped, scrypt_n=slot.scrypt_n,
                         scrypt_r=slot.scrypt_r, scrypt_p=slot.scrypt_p)
    assert unwrap_data_key(renamed, "pass phrase here") is None


def test_two_slots_for_the_same_secret_differ():
    """Per-slot salts. Identical wrappings would tell a thief that two doors
    share a secret without opening either."""
    dk = new_data_key()
    a = wrap_data_key(dk, "same secret here", "a", FAST)
    b = wrap_data_key(dk, "same secret here", "b", FAST)
    assert a.salt != b.salt and a.wrapped != b.wrapped


# --------------------------------------------------------------------------- #
# The keyring — the thing vault.py promised and never built
# --------------------------------------------------------------------------- #

def test_every_enrolled_door_opens_the_same_vault():
    """THE point. can_select forces a passphrase slot and a proven recovery key
    behind whatever daily unlock is picked; before this module, neither existed
    as a door, because nothing ever called luksAddKey."""
    dk = new_data_key()
    ring = _ring(dk, daily="0-4-8-7", passphrase="a real passphrase",
                 recovery="ABCD-EFGH-JKMN-PQRS")
    for secret in ("0-4-8-7", "a real passphrase", "ABCD-EFGH-JKMN-PQRS"):
        key, name = ring.open(secret)
        assert key == dk, f"{secret!r} did not open the vault"
    assert ring.open("not any of them") == (None, None)


def test_open_names_the_door_that_worked():
    dk = new_data_key()
    ring = _ring(dk, daily="pattern here", recovery="recovery key here")
    assert ring.open("recovery key here")[1] == SLOT_RECOVERY


def test_losing_one_door_does_not_lose_the_vault():
    dk = new_data_key()
    ring = _ring(dk, daily="forgotten pattern", passphrase="still known here")
    ring.remove(SLOT_DAILY)
    assert ring.open("still known here")[0] == dk
    assert ring.open("forgotten pattern") == (None, None)


def test_the_last_door_cannot_be_removed():
    ring = _ring(new_data_key(), passphrase="only way in here")
    with pytest.raises(VaultError):
        ring.remove(SLOT_PASSPHRASE)


def test_the_recovery_slot_cannot_be_removed():
    """The operator was made to write it down and type it back. Letting it be
    deleted afterwards makes a forgotten passphrase final."""
    ring = _ring(new_data_key(), daily="a pattern here",
                 recovery="the recovery key")
    with pytest.raises(VaultError):
        ring.remove(SLOT_RECOVERY)


def test_changing_a_passphrase_retires_the_old_one():
    """Re-adding replaces. Appending would leave the OLD passphrase working —
    the stale-keyslot bug, where a changed secret still opens the vault."""
    dk = new_data_key()
    ring = _ring(dk, passphrase="the old passphrase")
    ring.add(dk, "the new passphrase", SLOT_PASSPHRASE, FAST)
    assert len(ring.slots) == 1
    assert ring.open("the new passphrase")[0] == dk
    assert ring.open("the old passphrase") == (None, None)


def test_keyring_survives_a_round_trip_through_json():
    dk = new_data_key()
    ring = _ring(dk, daily="a daily secret", recovery="a recovery key")
    back = Keyring.from_json(ring.to_json())
    assert back.names() == ring.names()
    assert back.open("a recovery key")[0] == dk


def test_a_newer_format_is_refused_rather_than_guessed_at():
    ring = _ring(new_data_key(), passphrase="a passphrase here")
    d = json.loads(ring.to_json())
    d["version"] = 99
    with pytest.raises(VaultError, match="newer medic"):
        Keyring.from_json(json.dumps(d))


def test_a_keyring_with_no_slots_is_refused():
    with pytest.raises(VaultError):
        Keyring.from_json(json.dumps({"version": 1, "slots": []}))


def test_a_corrupt_keyring_says_so_plainly():
    with pytest.raises(VaultError, match="not readable JSON"):
        Keyring.from_json("{not json")


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #

def test_a_file_round_trips():
    dk = new_data_key()
    blob = encrypt_bytes(dk, b"node FAITH, last seen 2026-09-02", "nodes/faith")
    assert is_encrypted(blob) and blob.startswith(MAGIC)
    assert b"FAITH" not in blob
    assert decrypt_bytes(dk, blob, "nodes/faith") == b"node FAITH, last seen 2026-09-02"


def test_a_file_moved_inside_the_vault_is_detected():
    """The path is authenticated. Swapping one node's record for another's is
    the edit worth making, and it needs no key to attempt."""
    dk = new_data_key()
    blob = encrypt_bytes(dk, b"trusted: yes", "nodes/faith")
    with pytest.raises(VaultError, match="moved from somewhere else"):
        decrypt_bytes(dk, blob, "nodes/hawkeye")


def test_a_tampered_file_is_detected():
    dk = new_data_key()
    blob = bytearray(encrypt_bytes(dk, b"trusted: no", "nodes/x"))
    blob[-1] ^= 0x01
    with pytest.raises(VaultError, match="integrity check"):
        decrypt_bytes(dk, bytes(blob), "nodes/x")


def test_the_wrong_key_cannot_read_a_file():
    blob = encrypt_bytes(new_data_key(), b"secret", "a")
    with pytest.raises(VaultError):
        decrypt_bytes(new_data_key(), blob, "a")


def test_plaintext_is_not_mistaken_for_ciphertext():
    with pytest.raises(VaultError, match="not an encrypted vault file"):
        decrypt_bytes(new_data_key(), b"just a plain file", "a")


# --------------------------------------------------------------------------- #
# Trees
# --------------------------------------------------------------------------- #

def _tree(tmp_path):
    root = tmp_path / "records"
    (root / "nodes").mkdir(parents=True)
    (root / "nodes" / "faith.json").write_bytes(b'{"name": "FAITH"}')
    (root / "nodes" / "hawkeye.json").write_bytes(b'{"name": "HAWKEYE"}')
    (root / "registry.json").write_bytes(b'{"kin": 2}')
    return root


def test_a_tree_round_trips_and_the_names_stay_readable(tmp_path):
    """Filenames stay in the clear. Encrypting them too would mean an operator
    with a card reader and the recovery key still could not tell which file was
    which — and the threat here is a pulled SD card, not a filesystem audit."""
    root = _tree(tmp_path)
    dk = new_data_key()
    changed = encrypt_tree(dk, str(root))
    assert sorted(changed) == ["nodes/faith.json", "nodes/hawkeye.json",
                               "registry.json"]
    assert b"FAITH" not in (root / "nodes" / "faith.json").read_bytes()
    assert (root / "nodes" / "faith.json").exists()

    decrypt_tree(dk, str(root))
    assert (root / "nodes" / "faith.json").read_bytes() == b'{"name": "FAITH"}'
    assert (root / "registry.json").read_bytes() == b'{"kin": 2}'


def test_a_second_pass_does_not_double_encrypt(tmp_path):
    """The state a power cut leaves behind. Re-running must finish the job, not
    encrypt the finished files again with the same key."""
    root = _tree(tmp_path)
    dk = new_data_key()
    encrypt_tree(dk, str(root))
    first = (root / "registry.json").read_bytes()
    assert encrypt_tree(dk, str(root)) == []
    assert (root / "registry.json").read_bytes() == first
    decrypt_tree(dk, str(root))
    assert (root / "registry.json").read_bytes() == b'{"kin": 2}'


def test_a_half_encrypted_tree_is_finished_not_broken(tmp_path):
    root = _tree(tmp_path)
    dk = new_data_key()
    encrypt_tree(dk, str(root))
    decrypt_bytes(dk, (root / "registry.json").read_bytes(), "registry.json")
    (root / "nodes" / "new.json").write_bytes(b'{"name": "NEW"}')
    assert encrypt_tree(dk, str(root)) == ["nodes/new.json"]
    decrypt_tree(dk, str(root))
    assert (root / "nodes" / "new.json").read_bytes() == b'{"name": "NEW"}'


def test_the_keyring_is_never_encrypted(tmp_path):
    """Encrypting the file that holds the key is the one edit that makes the
    vault unopenable."""
    root = _tree(tmp_path)
    dk = new_data_key()
    save_keyring(str(root), _ring(dk, passphrase="a passphrase here"))
    encrypt_tree(dk, str(root))
    assert not is_encrypted((root / KEYRING_NAME).read_bytes())
    assert load_keyring(str(root)).open("a passphrase here")[0] == dk


def test_symlinks_are_not_followed(tmp_path):
    """Following one would encrypt whatever it points at — for
    ~/.reticulum-node-medic that could reach outside the records entirely."""
    root = _tree(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"NOT MINE")
    os.symlink(str(outside), str(root / "link.json"))
    encrypt_tree(new_data_key(), str(root))
    assert outside.read_bytes() == b"NOT MINE"


def test_is_vault_answers_from_the_keyring(tmp_path):
    root = _tree(tmp_path)
    assert is_vault(str(root)) is False
    save_keyring(str(root), _ring(new_data_key(), passphrase="a passphrase!"))
    assert is_vault(str(root)) is True


def test_the_keyring_is_not_world_readable(tmp_path):
    root = _tree(tmp_path)
    path = save_keyring(str(root), _ring(new_data_key(), passphrase="pass here!"))
    assert oct(os.stat(path).st_mode)[-3:] == "600"


def test_loading_a_vault_that_is_not_one_says_so(tmp_path):
    with pytest.raises(VaultError, match="not encrypted"):
        load_keyring(str(tmp_path))


def test_no_temp_files_are_left_behind(tmp_path):
    """The atomic write uses a sibling temp file. One left behind would be
    PLAINTEXT sitting next to the ciphertext it was meant to replace."""
    root = _tree(tmp_path)
    dk = new_data_key()
    encrypt_tree(dk, str(root))
    leftovers = [f for _, _, fs in os.walk(str(root)) for f in fs
                 if f.endswith(".rnmtmp")]
    assert leftovers == []


# --------------------------------------------------------------------------- #
# Switching it on and off — the thing the Settings switch calls
# --------------------------------------------------------------------------- #

def _records(tmp_path):
    root = tmp_path / ".reticulum-node-medic"
    (root / "nodes").mkdir(parents=True)
    (root / "nodes" / "faith.json").write_bytes(b'{"name": "FAITH", "trust": 3}')
    (root / "registry.json").write_bytes(b'{"kin": 1}')
    return root


def test_enable_encrypts_and_installs_all_three_doors(tmp_path, monkeypatch):
    from provisioning import records_vault as rv
    monkeypatch.setattr(rv, "WRAP_PARAMS", FAST)
    root = _records(tmp_path)
    res = rv.enable_vault(str(root), "0-4-8-7", "a real passphrase",
                          "ABCD-EFGH-JKMN", params=FAST)
    assert res["files"] == 2
    assert sorted(res["doors"]) == [SLOT_DAILY, SLOT_PASSPHRASE, SLOT_RECOVERY]
    assert b"FAITH" not in (root / "nodes" / "faith.json").read_bytes()
    for secret in ("0-4-8-7", "a real passphrase", "ABCD-EFGH-JKMN"):
        assert rv.open_vault(str(root), secret)[0] is not None


def test_disable_gives_the_records_back(tmp_path, monkeypatch):
    """The way OUT. Without it a forgotten passphrase and a format change are
    the same disaster."""
    from provisioning import records_vault as rv
    monkeypatch.setattr(rv, "WRAP_PARAMS", FAST)
    root = _records(tmp_path)
    rv.enable_vault(str(root), "pattern here", "a real passphrase",
                    "ABCD-EFGH", params=FAST)
    res = rv.disable_vault(str(root), "ABCD-EFGH")
    assert res["opened_with"] == SLOT_RECOVERY
    assert (root / "nodes" / "faith.json").read_bytes() == \
        b'{"name": "FAITH", "trust": 3}'
    assert (root / "registry.json").read_bytes() == b'{"kin": 1}'
    assert not (root / KEYRING_NAME).exists()


def test_enable_refuses_to_run_twice(tmp_path, monkeypatch):
    from provisioning import records_vault as rv
    monkeypatch.setattr(rv, "WRAP_PARAMS", FAST)
    root = _records(tmp_path)
    rv.enable_vault(str(root), "a", "b", "c", params=FAST)
    with pytest.raises(VaultError, match="already encrypted"):
        rv.enable_vault(str(root), "a", "b", "c", params=FAST)


def test_enable_refuses_a_missing_door(tmp_path, monkeypatch):
    """Encrypting with an empty recovery key would install a door anyone opens,
    and the keyring would look entirely normal."""
    from provisioning import records_vault as rv
    root = _records(tmp_path)
    with pytest.raises(VaultError, match="recovery key"):
        rv.enable_vault(str(root), "a", "b", "", params=FAST)
    assert not (root / KEYRING_NAME).exists()
    assert (root / "registry.json").read_bytes() == b'{"kin": 1}'


def test_the_keyring_is_written_before_the_files_are_encrypted(tmp_path,
                                                               monkeypatch):
    """Power-cut ordering. A keyring plus a half-encrypted tree is recoverable;
    an encrypted tree whose data key was never saved is not."""
    from provisioning import records_vault as rv
    monkeypatch.setattr(rv, "WRAP_PARAMS", FAST)
    root = _records(tmp_path)
    seen = {}

    real = rv.encrypt_tree
    def spy(dk, r):
        seen["keyring_on_disk"] = os.path.exists(rv.keyring_path(r))
        return real(dk, r)
    monkeypatch.setattr(rv, "encrypt_tree", spy)
    rv.enable_vault(str(root), "a", "b", "c", params=FAST)
    assert seen["keyring_on_disk"] is True


def test_a_power_cut_mid_encrypt_is_recoverable(tmp_path, monkeypatch):
    from provisioning import records_vault as rv
    monkeypatch.setattr(rv, "WRAP_PARAMS", FAST)
    root = _records(tmp_path)
    real = rv.encrypt_tree
    monkeypatch.setattr(rv, "encrypt_tree",
                        lambda dk, r: (_ for _ in ()).throw(OSError("power")))
    with pytest.raises(OSError):
        rv.enable_vault(str(root), "a", "b", "recovery here", params=FAST)

    # The keyring survived, so the data key is not lost. Finish the job.
    monkeypatch.setattr(rv, "encrypt_tree", real)
    dk, _ = rv.open_vault(str(root), "recovery here")
    rv.encrypt_tree(dk, str(root))
    assert rv.disable_vault(str(root), "recovery here")["files"] == 2
    assert (root / "registry.json").read_bytes() == b'{"kin": 1}'


def test_open_vault_says_so_plainly_when_nothing_opens(tmp_path, monkeypatch):
    from provisioning import records_vault as rv
    monkeypatch.setattr(rv, "WRAP_PARAMS", FAST)
    root = _records(tmp_path)
    rv.enable_vault(str(root), "a", "b", "c", params=FAST)
    with pytest.raises(VaultError, match="did not open"):
        rv.open_vault(str(root), "not a door")


def test_records_root_is_read_off_the_migration_config():
    """Spelled once. The screens and the planner disagreed about scope before."""
    from provisioning.records_vault import records_root
    from provisioning.vault import RECORDS_ROOTS
    assert records_root("/home/x") == f"/home/x/{RECORDS_ROOTS[0]}"


# --------------------------------------------------------------------------- #
# What is NOT encrypted, and how fast a known door opens (2026-09-03)
# --------------------------------------------------------------------------- #

def test_the_map_tiles_are_not_encrypted(tmp_path, monkeypatch):
    """714 MB of the medic's 716 MB records are public OpenStreetMap tiles.
    Encrypting them took 20.5s of a 20.7s run and protected nothing."""
    from provisioning import records_vault as rv
    root = tmp_path / ".reticulum-node-medic"
    (root / "maps").mkdir(parents=True)
    (root / "maps" / "offline.mbtiles").write_bytes(b"SQLite format 3\x00tiles")
    (root / "registry.json").write_bytes(b'{"kin": 1}')
    changed = rv.encrypt_tree(rv.new_data_key(), str(root))
    assert changed == ["registry.json"]
    assert (root / "maps" / "offline.mbtiles").read_bytes().startswith(b"SQLite")


def test_only_top_level_maps_is_skipped(tmp_path):
    """A directory called "maps" further down is somebody's records, not the
    tile store. Skipping it would silently leave real data in the clear."""
    from provisioning import records_vault as rv
    root = tmp_path / ".reticulum-node-medic"
    (root / "nodes" / "maps").mkdir(parents=True)
    (root / "nodes" / "maps" / "where.json").write_bytes(b'{"lat": 1}')
    assert rv.encrypt_tree(rv.new_data_key(), str(root)) == ["nodes/maps/where.json"]


def test_a_known_door_tries_only_that_slot(tmp_path, monkeypatch):
    """1.6s instead of 4.8s. The unlock screen knows which door the operator
    picked, because they picked it."""
    from provisioning import records_vault as rv
    dk = new_data_key()
    ring = _ring(dk, daily="0-4-8-7", passphrase="a real passphrase",
                 recovery="ABCD-EFGH")
    tried = []
    real = rv.unwrap_data_key
    monkeypatch.setattr(rv, "unwrap_data_key",
                        lambda s, sec: (tried.append(s.name), real(s, sec))[1])
    assert ring.open("a real passphrase", only=SLOT_PASSPHRASE)[0] == dk
    assert tried == [SLOT_PASSPHRASE]


def test_a_known_door_does_not_open_with_another_doors_secret(tmp_path):
    """Naming the slot must not become a way to skip the check."""
    dk = new_data_key()
    ring = _ring(dk, daily="0-4-8-7", passphrase="a real passphrase")
    assert ring.open("0-4-8-7", only=SLOT_PASSPHRASE) == (None, None)


def test_an_unknown_door_name_opens_nothing(tmp_path):
    ring = _ring(new_data_key(), passphrase="a real passphrase")
    assert ring.open("a real passphrase", only="no-such-slot") == (None, None)


def test_the_recovery_path_still_tries_every_slot(tmp_path, monkeypatch):
    """When the operator types a string and the medic does not know which door
    it is, every slot is tried and none returns early."""
    from provisioning import records_vault as rv
    dk = new_data_key()
    ring = _ring(dk, daily="0-4-8-7", passphrase="a real passphrase",
                 recovery="ABCD-EFGH")
    tried = []
    real = rv.unwrap_data_key
    monkeypatch.setattr(rv, "unwrap_data_key",
                        lambda s, sec: (tried.append(s.name), real(s, sec))[1])
    assert ring.open("0-4-8-7")[1] == SLOT_DAILY
    assert len(tried) == 3, "returned early and leaked which door matched"
