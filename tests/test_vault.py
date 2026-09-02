"""Encrypt-at-rest vault — pure-logic unit tests + a throwaway-tree integration
test. Never touches real medic data or a real block device."""

import os
import shutil
import socket
import tempfile

import pytest

from provisioning import vault
from ui.vault_unlock import answer_pending_ask_password, find_pending_socket
from provisioning.vault import (
    Argon2idParams,
    ScryptParams,
    VaultConfig,
    _SUPERSEDED_SENSITIVE_ROOTS,
    apply_plan,
    build_luks_format_argv,
    build_luks_open_argv,
    derive_key,
    plan_migration,
    revert_plan,
)


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

def test_default_config_validates():
    VaultConfig().validate()  # must not raise


def test_config_rejects_tiny_vault():
    with pytest.raises(ValueError):
        VaultConfig(size_mb=1).validate()


def test_config_rejects_weak_argon2():
    with pytest.raises(ValueError):
        VaultConfig(argon2=Argon2idParams(memory_kib=1024)).validate()


def test_config_rejects_unsafe_mapper_name():
    with pytest.raises(ValueError):
        VaultConfig(mapper_name="evil; rm -rf /").validate()


def test_expanded_resolves_home():
    cfg = VaultConfig().expanded("/home/tester")
    assert cfg.container_path == "/home/tester/.nodemedic-vault.img"
    assert cfg.mount_point == "/home/tester/.nodemedic-vault"


# --------------------------------------------------------------------------- #
# KDF — argon2id (LUKS) params surface in the cryptsetup argv
# --------------------------------------------------------------------------- #

def test_luks_format_argv_uses_argon2id_with_params():
    a = Argon2idParams(memory_kib=262144, parallelism=4, iter_time_ms=2000)
    argv = build_luks_format_argv("/dev/loop7", a)
    assert argv[:2] == ["cryptsetup", "luksFormat"]
    assert "argon2id" in argv
    # explicit cost params are present, not left to auto-benchmark
    assert "--pbkdf-memory" in argv and "262144" in argv
    assert "--pbkdf-parallel" in argv and "4" in argv
    assert "--iter-time" in argv and "2000" in argv
    # LUKS2 + AES-XTS-512, and passphrase only ever via stdin
    assert "luks2" in argv and "aes-xts-plain64" in argv
    assert argv[-2:] == ["-", "/dev/loop7"]  # --key-file -  <device>


def test_luks_open_argv_rejects_injection():
    with pytest.raises(ValueError):
        build_luks_open_argv("/dev/loop7", "name;reboot")


def test_luks_open_argv_ok():
    argv = build_luks_open_argv("/dev/loop7", "nodemedic_vault")
    assert argv[-2:] == ["/dev/loop7", "nodemedic_vault"]
    assert "--key-file" in argv


# --------------------------------------------------------------------------- #
# KDF — scrypt (USB-keyfile path)
# --------------------------------------------------------------------------- #

def test_derive_key_length_and_determinism():
    salt = b"0123456789abcdef"
    k1 = derive_key("correct horse battery staple", salt)
    k2 = derive_key("correct horse battery staple", salt)
    assert len(k1) == 32
    assert k1 == k2                       # deterministic for same inputs


def test_derive_key_diverges_on_salt_and_passphrase():
    k1 = derive_key("pass", b"0123456789abcdef")
    k2 = derive_key("pass", b"fedcba9876543210")
    k3 = derive_key("other", b"0123456789abcdef")
    assert k1 != k2 and k1 != k3


def test_derive_key_rejects_short_salt():
    with pytest.raises(ValueError):
        derive_key("pass", b"short")


def test_scrypt_maxmem_covers_cost():
    p = ScryptParams()
    assert p.maxmem >= 128 * p.r * p.n


# --------------------------------------------------------------------------- #
# Migration planning (pure)
# --------------------------------------------------------------------------- #

def test_the_mesh_identity_stays_OUT_of_the_vault():
    """RECORDS-ONLY is the shape, and this is the test that keeps it that way.

    ~/.reticulum and ~/.lxmd must NEVER be migrated. Inside the vault, a locked
    medic cannot start rnsd/lxmd - so a power cut takes the node off the air
    until a human walks to it, which is a worse failure than a readable card.
    Operator, 2026-08-02 and again 2026-09-02: "definitely lean to the side of
    keeping nodes active in the wild".

    The tuple existed and was documented; the DEFAULT was the other one, so
    every script would have migrated the identity while the setup screen
    promised it would not."""
    from provisioning.vault import plan_migration, RECORDS_ROOTS
    steps = plan_migration("/home/nodemedic")
    sources = {st.source for st in steps}
    assert sources == {"/home/nodemedic/.reticulum-node-medic"}
    assert "/home/nodemedic/.reticulum" not in sources, "mesh identity migrated"
    assert "/home/nodemedic/.lxmd" not in sources, "LXMF identity migrated"


# The pre-2026-08-02 three-root shape. Kept as an EXPLICIT test fixture: cards
# imaged before that ruling have the mesh identity inside the vault, so revert
# must keep working against it even though nothing plans it any more.
_MULTI_ROOT = VaultConfig(roots=_SUPERSEDED_SENSITIVE_ROOTS)


def _superseded_plan_covers_all_sensitive_roots():
    steps = plan_migration("/home/nodemedic")
    sources = {s.source for s in steps}
    assert "/home/nodemedic/.lxmd" in sources
    assert "/home/nodemedic/.reticulum" in sources
    assert "/home/nodemedic/.reticulum-node-medic" in sources


def test_plan_targets_inside_mount_and_drops_leading_dot():
    """Mechanics, not policy. Named explicitly via config so this keeps
    covering multi-root relocation without re-pinning it as the default -
    the revert path still has to unwind cards migrated under the old shape."""
    steps = plan_migration("/home/nodemedic", _MULTI_ROOT)
    by_name = {s.name: s for s in steps}
    assert by_name["lxmd"].vault_target == "/home/nodemedic/.nodemedic-vault/lxmd"
    assert by_name["reticulum"].backup.endswith(".pre-vault.bak")


# --------------------------------------------------------------------------- #
# Integration: apply + revert against a THROWAWAY temp tree with DUMMY data.
# Stands in for "vault mounted at mount_point"; no crypto/loop/root needed, so
# it runs anywhere (Mac/CI). The real LUKS container is exercised by
# scripts/vault_create.sh on the medic (see docs/encrypt-at-rest.md).
# --------------------------------------------------------------------------- #

def _fake_home_with_dummy_data(tmp_path):
    home = tmp_path / "home"
    # dummy .lxmd/identity + .reticulum/storage/transport_identity + medic data
    (home / ".lxmd").mkdir(parents=True)
    (home / ".lxmd" / "identity").write_bytes(b"DUMMY-LXMF-KEY")
    (home / ".reticulum" / "storage").mkdir(parents=True)
    (home / ".reticulum" / "storage" / "transport_identity").write_bytes(b"DUMMY-RNS-KEY")
    (home / ".reticulum" / "config").write_text("port = /tmp/rnode-jonesey")
    (home / ".reticulum-node-medic").mkdir(parents=True)
    (home / ".reticulum-node-medic" / "kin.json").write_text('{"kin": []}')
    return home


def test_apply_then_revert_roundtrip(tmp_path):
    home = _fake_home_with_dummy_data(tmp_path)
    mount = home / ".nodemedic-vault"     # stands in for the mounted vault
    mount.mkdir()

    steps = plan_migration(str(home), _MULTI_ROOT)
    migrated = apply_plan(steps)
    assert len(migrated) == 3

    # originals are now symlinks pointing into the "vault", data readable through
    assert os.path.islink(str(home / ".lxmd"))
    assert (home / ".lxmd" / "identity").read_bytes() == b"DUMMY-LXMF-KEY"
    assert (home / ".reticulum" / "storage" / "transport_identity").read_bytes() \
        == b"DUMMY-RNS-KEY"
    # data physically lives in the vault dir
    assert (mount / "lxmd" / "identity").read_bytes() == b"DUMMY-LXMF-KEY"
    # pre-migration backup preserved (nothing destroyed)
    assert (tmp_path / "home" / (".lxmd" + vault.BACKUP_SUFFIX)).exists()

    # idempotent: applying again is a no-op (already symlinks)
    assert apply_plan(steps) == []

    # rollback restores real dirs and the mesh keys survive
    restored = revert_plan(steps)
    assert len(restored) == 3
    assert not os.path.islink(str(home / ".lxmd"))
    assert (home / ".lxmd" / "identity").read_bytes() == b"DUMMY-LXMF-KEY"
    assert (home / ".reticulum-node-medic" / "kin.json").read_text() == '{"kin": []}'


def test_apply_handles_missing_source(tmp_path):
    # a daemon that never ran yet has no dir; apply should pre-create it in-vault
    home = tmp_path / "home"
    home.mkdir()
    (home / ".nodemedic-vault").mkdir()
    steps = plan_migration(str(home), _MULTI_ROOT)
    apply_plan(steps)
    for s in steps:
        assert os.path.islink(s.source)
        assert os.path.isdir(s.vault_target)


def test_apply_refuses_to_clobber_existing_vault_target(tmp_path):
    home = _fake_home_with_dummy_data(tmp_path)
    mount = home / ".nodemedic-vault"
    mount.mkdir()
    (mount / "lxmd").mkdir()              # a stale target already there
    (mount / "lxmd" / "identity").write_bytes(b"STALE")
    with pytest.raises(FileExistsError):
        apply_plan(plan_migration(str(home), _MULTI_ROOT))


# --------------------------------------------------------------------------- #
# Optional: real LUKS loopback container. Skipped unless cryptsetup + losetup +
# root are all present (i.e. on the medic, human-run). Kept tiny + fully
# self-cleaning; DUMMY data only, in a temp file — never a real device.
# --------------------------------------------------------------------------- #

_HAVE_LUKS = (
    shutil.which("cryptsetup") and shutil.which("losetup")
    and os.geteuid() == 0
)


# --------------------------------------------------------------------------- #
# Touchscreen unlock — systemd password-agent answerer, against a fake ask dir
# and a REAL temp AF_UNIX datagram socket (no root, runs anywhere).
# --------------------------------------------------------------------------- #

def _write_ask_file(ask_dir, sock_path):
    ask = ask_dir / "ask.abcd"
    ask.write_text(f"[Ask]\nPID=1\nSocket={sock_path}\nNotAfter=0\n")
    return ask


def test_find_pending_socket_none_when_empty(tmp_path):
    (tmp_path / "ask").mkdir()
    assert find_pending_socket(str(tmp_path / "ask")) is None


def test_answer_pending_ask_password_delivers(tmp_path):
    ask_dir = tmp_path / "ask"
    ask_dir.mkdir()
    # Short socket path: AF_UNIX paths are capped (~104 chars on macOS).
    sock_path = os.path.join(tempfile.mkdtemp(prefix="rnm", dir="/tmp"), "s")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(sock_path)
    srv.settimeout(2)
    try:
        _write_ask_file(ask_dir, sock_path)
        assert find_pending_socket(str(ask_dir)) == sock_path
        ok = answer_pending_ask_password("hunter2", ask_dir=str(ask_dir))
        assert ok is True
        data, _ = srv.recvfrom(256)
        assert data == b"+hunter2"        # '+' prefix = accept, per systemd proto
    finally:
        srv.close()
        shutil.rmtree(os.path.dirname(sock_path), ignore_errors=True)


def test_answer_returns_false_with_no_request(tmp_path):
    ask_dir = tmp_path / "ask"
    ask_dir.mkdir()
    assert answer_pending_ask_password("x", ask_dir=str(ask_dir)) is False


@pytest.mark.skipif(not _HAVE_LUKS, reason="needs cryptsetup+losetup+root")
def test_real_luks_container_roundtrip(tmp_path):  # pragma: no cover (medic-only)
    import subprocess

    img = tmp_path / "throwaway.img"
    mapper = "nodemedic_vault_test"
    mnt = tmp_path / "mnt"
    mnt.mkdir()
    passphrase = b"throwaway-test-passphrase"
    subprocess.run(["dd", "if=/dev/zero", f"of={img}", "bs=1M", "count=32"],
                   check=True)
    loop = subprocess.run(["losetup", "--find", "--show", str(img)],
                          check=True, capture_output=True, text=True).stdout.strip()
    try:
        subprocess.run(build_luks_format_argv(loop, Argon2idParams(
            memory_kib=8192, parallelism=1, iter_time_ms=200)),
            input=passphrase, check=True)
        subprocess.run(build_luks_open_argv(loop, mapper),
                       input=passphrase, check=True)
        try:
            subprocess.run(["mkfs.ext4", "-q", f"/dev/mapper/{mapper}"], check=True)
            subprocess.run(["mount", f"/dev/mapper/{mapper}", str(mnt)], check=True)
            try:
                (mnt / "secret").write_bytes(b"DUMMY")
                assert (mnt / "secret").read_bytes() == b"DUMMY"
            finally:
                subprocess.run(["umount", str(mnt)], check=True)
        finally:
            subprocess.run(["cryptsetup", "close", mapper], check=True)
    finally:
        subprocess.run(["losetup", "-d", loop], check=True)
