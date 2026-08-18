"""Writing the vault's USB key — the three ways a stick loses a vault.

Pure, Kivy-free. Every file operation is injected, so none of this needs a
stick, a mount point or root.
"""

import pytest

from provisioning import usb_key as uk
from provisioning.vault_factors import KEYFILE_NAME, keyfile_secret


# --- the stick that belongs to a different medic ----------------------------

def test_an_existing_key_file_is_never_overwritten():
    """nodemedic.key is ONE filename on every stick this project has written.
    The stick in the operator's hand may be the one that unlocks their other
    medic — and a silent overwrite is not a hypothetical, it is the default
    outcome of plugging in the wrong stick. The medic it bricks is not the one
    on the bench, so nothing on this bench would ever show it."""
    with pytest.raises(uk.KeyfileError) as e:
        uk.write_key("/media/pi/KEYS", exists=lambda _p: True)
    assert "another Node Medic" in str(e.value)
    assert KEYFILE_NAME in str(e.value)


def test_the_refusal_names_the_way_out():
    """A refusal with no next move is a dead end on a screen with no keyboard."""
    with pytest.raises(uk.KeyfileError) as e:
        uk.write_key("/media/pi/KEYS", exists=lambda _p: True)
    assert "different stick" in str(e.value)


# --- the stick that says yes and does nothing -------------------------------

def test_a_write_that_cannot_be_read_back_is_a_failure():
    """A stick mounted read-only, a dying stick, and a full stick all ACCEPT a
    write and report success. The difference only appears on the read, and if
    the medic does not look, the failure surfaces at the one moment the key is
    needed."""
    store = {}
    with pytest.raises(uk.KeyfileError) as e:
        uk.write_key("/media/pi/KEYS",
                     exists=lambda _p: False,
                     writer=lambda _p, _d: None,          # silently discards
                     reader=lambda p: store.get(p, b""))
    assert "not what was written" in str(e.value)


def test_a_stick_that_refuses_the_write_says_so_plainly():
    with pytest.raises(uk.KeyfileError) as e:
        uk.write_key("/media/pi/KEYS", exists=lambda _p: False,
                     writer=_raise(OSError("Read-only file system")))
    assert "Couldn't write to the stick" in str(e.value)
    assert "Read-only" in str(e.value)


def test_an_unreadable_write_is_not_reported_as_a_write():
    with pytest.raises(uk.KeyfileError) as e:
        uk.write_key("/media/pi/KEYS", exists=lambda _p: False,
                     writer=lambda _p, _d: None,
                     reader=_raise(OSError("Input/output error")))
    assert "couldn't read it back" in str(e.value)


def _raise(exc):
    def _fn(*_a, **_k):
        raise exc
    return _fn


# --- the happy path, only enough of it to prove the shape -------------------

def test_a_good_stick_returns_the_path_and_the_bytes_that_came_back():
    store = {}
    path, data = uk.write_key(
        "/media/pi/KEYS", exists=lambda _p: False,
        rand=lambda n: b"\x07" * n,
        writer=lambda p, d: store.__setitem__(p, d),
        reader=lambda p: store[p])
    assert path == "/media/pi/KEYS/" + KEYFILE_NAME
    assert data == b"\x07" * uk.KEY_BYTES
    assert keyfile_secret(data)               # long enough to be a factor


def test_the_key_is_long_enough_to_be_worth_calling_a_key():
    """keyfile_secret refuses anything under 16 bytes. A default that sat under
    that line would fail at the moment of writing, on the screen where the
    operator has a stick in their hand and no idea what to do about it."""
    assert uk.KEY_BYTES >= 32


def test_the_bytes_are_random_not_derived():
    """A key file's whole value is that it is not remembered. Deriving it from
    the passphrase or the serial number would make the stick a second copy of a
    factor the vault already has."""
    from tests.srcutil import src
    text = src("provisioning/usb_key.py")
    assert "secrets.token_bytes" in text


def test_the_write_is_flushed_to_the_stick():
    """The next thing the operator does is pull it out."""
    from tests.srcutil import func_source
    assert "fsync" in func_source("provisioning/usb_key.py", "_write_owner_only")


# --- finding the stick ------------------------------------------------------

def test_both_mount_shapes_are_found():
    """/media/<label> and /media/<user>/<label> both happen, and a medic that
    knew only one would report 'no USB stick' with a stick plugged in."""
    tree = {"/media": ["pi", "KEYS"], "/media/pi": ["STICK"], "/media/KEYS": []}
    out = uk.mount_points(lister=lambda p: tree.get(p, []))
    assert "/media/KEYS" in out
    assert "/media/pi/STICK" in out


def test_nothing_mounted_is_not_an_error():
    assert uk.mount_points(lister=lambda _p: []) == []


def test_two_sticks_are_not_chosen_between_by_the_medic():
    """Choosing for them means a key written to a device they were not thinking
    about — and the stick they WERE thinking about then does not open anything."""
    msg = uk.describe_targets(["/media/pi/A", "/media/pi/B"])
    assert "pick the one you mean" in msg
    assert "/media/pi/A" in msg and "/media/pi/B" in msg


def test_no_stick_says_so_without_pretending_to_have_looked_harder():
    msg = uk.describe_targets([])
    assert "No USB stick found" in msg


def test_one_stick_is_named_by_its_path():
    """With two plugged in the mount path is the only thing the medic can show
    that the operator can also check."""
    msg = uk.describe_targets(["/media/pi/KEYS"])
    assert "/media/pi/KEYS" in msg


def test_a_stick_already_carrying_a_key_is_recognised():
    assert uk.existing_key("/media/pi/KEYS", exists=lambda _p: True) == \
        "/media/pi/KEYS/" + KEYFILE_NAME
    assert uk.existing_key("/media/pi/KEYS", exists=lambda _p: False) is None


def test_the_permissions_promise_is_not_overstated():
    """A stick is usually vfat and carries no permissions at all. The protection
    on a USB key is that the operator holds it — saying otherwise on a screen
    would be the tool claiming something it has not checked."""
    from tests.srcutil import func_source
    doc = func_source("provisioning/usb_key.py", "_write_owner_only")
    assert "vfat" in doc and "carries none" in doc
