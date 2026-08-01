"""Encrypt-at-rest vault — planning + key-derivation logic for the medic.

The SD card holds the medic's private mesh identity keys (Reticulum transport
identity, LXMF identity, recalled-identity store) and a location/activity trail
(kin roster, node registry). ``provisioning.harden`` clamps these to owner-only,
which is ACCESS CONTROL — it does nothing for a card pulled and read on another
machine. This module is the ENCRYPTION-at-rest layer: the sensitive directories
live inside a single LUKS2-on-a-file container that is unlocked with a
passphrase at boot/app-start, mounted, and bind-symlinked back to their original
paths so rnsd / lxmd / the app find them unchanged.

This module is PURE LOGIC only — it never formats, mounts, or writes to real
devices. The privileged operations (losetup / cryptsetup / mkfs / mount) live in
``scripts/vault_*.sh`` and are human-run. What lives here:

  * the canonical configuration (container path, mapper name, mount point,
    size, KDF parameters) — the shell scripts mirror these values;
  * ``plan_migration`` — which real dirs relocate where, and the symlink that
    replaces each (unit-tested, no side effects);
  * ``apply_plan`` / ``revert_plan`` — move data + swap symlinks for a given
    plan, used by the migrate / rollback scripts and exercised by the
    integration test against a THROWAWAY temp dir (never real data);
  * ``derive_key`` — scrypt KDF (stdlib ``hashlib``) for the optional
    USB-keyfile key-wrapping path;
  * ``build_luks_format_argv`` / ``build_luks_open_argv`` — the exact cryptsetup
    argument vectors (argon2id KDF), so the KDF choice is testable.

See ``docs/encrypt-at-rest.md`` for the threat model and enable/rollback steps.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass, field
from typing import List, Optional

# --------------------------------------------------------------------------- #
# Canonical configuration.  scripts/vault_*.sh hardcode the same values; keep
# them in sync (the scripts note this file as the source of truth).
# --------------------------------------------------------------------------- #

#: The encrypted container file (a plain file on the SD, opened via a loop dev).
CONTAINER_PATH = "~/.nodemedic-vault.img"

#: dm-crypt mapper name -> /dev/mapper/<MAPPER_NAME> once luksOpen'd.
MAPPER_NAME = "nodemedic_vault"

#: Where the decrypted filesystem is mounted while unlocked.
MOUNT_POINT = "~/.nodemedic-vault"

#: Container size. The protected data is tiny (keys + a few JSON files); 256 MiB
#: is generous headroom and keeps argon2 unlock fast on a Pi 5.
SIZE_MB = 256

#: Directories relocated INTO the vault. Each becomes a symlink to
#: <mount>/<name> while unlocked; when the vault is locked the symlink dangles
#: and rnsd/lxmd/the app fail to start — that dangling link IS the gate that
#: keeps the daemons from running against absent keys. Whole roots are moved
#: (rather than individual key files) so a single mount covers everything and
#: the daemon-gate is unambiguous.
SENSITIVE_ROOTS = (
    ".reticulum-node-medic",   # kin.json, registry.json, certificates/, identity
    ".lxmd",                   # LXMF private identity
    ".reticulum",              # storage/transport_identity, storage/identities/, config
)

#: RECORDS-ONLY roots — the operator's chosen shape (2026-08-02). Only the
#: medic's own records go in the vault: the kin roster with every node's EXACT
#: coordinates, birth certificates, the GPS/activity history, the operator's
#: LXMF address. The MESH IDENTITY (~/.reticulum, ~/.lxmd) deliberately stays
#: OUTSIDE, so rnsd/lxmd start unattended and the medic rejoins the mesh by
#: itself after a power cut — it is a home propagation node, and losing it
#: from the mesh until someone walks home was judged the greater harm.
#: The trade, stated honestly: a stolen card reveals that this is a Reticulum
#: node and its mesh address, but NOT where the fleet is or who runs it.
RECORDS_ROOTS = (
    ".reticulum-node-medic",
)

#: Suffix for the pre-migration backup left in place (NOT deleted) so enabling
#: the vault is reversible even if a copy went wrong. The human removes these
#: after verifying the mesh still works.
BACKUP_SUFFIX = ".pre-vault.bak"


@dataclass(frozen=True)
class Argon2idParams:
    """LUKS2 argon2id KDF parameters (passed to ``cryptsetup luksFormat``).

    argon2id is memory-hard: it resists the GPU/ASIC brute-force that a thief
    would use against a pulled SD card. Sized to run in ~1-2 s on a Pi 5 while
    forcing a large memory cost on any offline guess.
    """

    memory_kib: int = 262144   # 256 MiB of memory cost per guess
    parallelism: int = 4       # Pi 5 has 4 cores
    iter_time_ms: int = 2000   # target unlock time; cryptsetup tunes iterations


@dataclass(frozen=True)
class ScryptParams:
    """scrypt KDF params for the OPTIONAL USB-keyfile wrapping path.

    Used only when deriving a 32-byte key file from a passphrase in Python
    (``derive_key``); the LUKS container's own KDF is argon2id (above). N=2**17
    ⇒ ~128 MiB memory cost (128 * r * N bytes)."""

    n: int = 1 << 17
    r: int = 8
    p: int = 1
    dklen: int = 32

    @property
    def maxmem(self) -> int:
        # hashlib.scrypt needs maxmem >= 128 * r * N; add headroom.
        return 128 * self.r * self.n * 2


@dataclass(frozen=True)
class VaultConfig:
    container_path: str = CONTAINER_PATH
    mapper_name: str = MAPPER_NAME
    mount_point: str = MOUNT_POINT
    size_mb: int = SIZE_MB
    roots: tuple = SENSITIVE_ROOTS
    argon2: Argon2idParams = field(default_factory=Argon2idParams)
    scrypt: ScryptParams = field(default_factory=ScryptParams)

    def expanded(self, home: Optional[str] = None) -> "VaultConfig":
        """Return a copy with ~ expanded against *home* (or $HOME)."""
        h = home or os.path.expanduser("~")

        def exp(p: str) -> str:
            if p.startswith("~"):
                return os.path.normpath(h + p[1:])
            return p

        from dataclasses import replace
        return replace(self, container_path=exp(self.container_path),
                       mount_point=exp(self.mount_point))

    def validate(self) -> None:
        if self.size_mb < 16:
            raise ValueError("vault too small; needs >= 16 MiB")
        if not self.mapper_name.replace("_", "").isalnum():
            raise ValueError(f"unsafe mapper name: {self.mapper_name!r}")
        if not self.roots:
            raise ValueError("no sensitive roots configured")
        if self.argon2.memory_kib < 8192:
            raise ValueError("argon2 memory cost too low (< 8 MiB)")


# --------------------------------------------------------------------------- #
# Migration planning (pure).
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class MigrationStep:
    """One relocation: *source* (a real dir under home) moves to *vault_target*
    (inside the mounted vault) and *source* is replaced by a symlink to it."""

    source: str          # e.g. /home/nodemedic/.lxmd
    vault_target: str    # e.g. /home/nodemedic/.nodemedic-vault/lxmd
    name: str            # e.g. lxmd

    @property
    def backup(self) -> str:
        return self.source + BACKUP_SUFFIX


def plan_migration(home: str, config: Optional[VaultConfig] = None) -> List[MigrationStep]:
    """Return the ordered relocation plan for *home*. Pure — touches nothing."""
    cfg = (config or VaultConfig()).expanded(home)
    steps: List[MigrationStep] = []
    for root in cfg.roots:
        name = root[1:] if root.startswith(".") else root  # drop leading dot
        steps.append(MigrationStep(
            source=os.path.join(home, root),
            vault_target=os.path.join(cfg.mount_point, name),
            name=name,
        ))
    return steps


# --------------------------------------------------------------------------- #
# Migration application. Used by scripts/vault_migrate.sh (AFTER the vault is
# mounted at cfg.mount_point) and by the integration test against a temp dir.
# Never call with a real home unless the vault is genuinely mounted.
# --------------------------------------------------------------------------- #

def apply_plan(steps: List[MigrationStep]) -> List[str]:
    """Move each source into the (already-mounted) vault and replace it with a
    symlink. Copy-then-backup: the original is renamed aside (BACKUP_SUFFIX),
    never deleted, so a bad copy is recoverable. Idempotent — a source that is
    already a symlink is skipped. Returns the sources that were migrated."""
    migrated: List[str] = []
    for step in steps:
        src = step.source
        if os.path.islink(src):
            continue                      # already migrated
        if not os.path.exists(src):
            # Nothing there yet (e.g. daemon never ran). Create the target dir
            # inside the vault and symlink, so first run lands in the vault.
            os.makedirs(step.vault_target, exist_ok=True)
            os.symlink(step.vault_target, src)
            migrated.append(src)
            continue
        if os.path.exists(step.vault_target):
            raise FileExistsError(
                f"vault target already exists: {step.vault_target}")
        shutil.copytree(src, step.vault_target, symlinks=True)
        os.rename(src, step.backup)       # keep the original as a backup
        os.symlink(step.vault_target, src)
        migrated.append(src)
    return migrated


def revert_plan(steps: List[MigrationStep]) -> List[str]:
    """Undo apply_plan: copy each vault target back to its real path and drop
    the symlink. Leaves the vault contents intact. Used by the rollback script
    (vault mounted) and the integration test. Returns restored sources."""
    restored: List[str] = []
    for step in steps:
        src = step.source
        if os.path.islink(src):
            os.unlink(src)
        elif os.path.exists(src):
            continue                      # a real dir already sits here; leave it
        if os.path.exists(step.vault_target):
            shutil.copytree(step.vault_target, src, symlinks=True)
            restored.append(src)
        elif os.path.exists(step.backup):
            os.rename(step.backup, src)   # fall back to the pre-migration backup
            restored.append(src)
    return restored


# --------------------------------------------------------------------------- #
# Key derivation (optional USB-keyfile path).
# --------------------------------------------------------------------------- #

def derive_key(passphrase: str, salt: bytes,
               params: Optional[ScryptParams] = None) -> bytes:
    """Derive a 32-byte key from *passphrase* + *salt* via scrypt (stdlib).

    Deterministic for a given (passphrase, salt, params). Used to turn a
    passphrase into a LUKS key file for the USB-keyfile unlock option; the
    default passphrase container uses argon2id inside cryptsetup instead."""
    p = params or ScryptParams()
    if not isinstance(salt, (bytes, bytearray)) or len(salt) < 16:
        raise ValueError("salt must be >= 16 random bytes")
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=bytes(salt),
                          n=p.n, r=p.r, p=p.p, dklen=p.dklen, maxmem=p.maxmem)


# --------------------------------------------------------------------------- #
# cryptsetup argument vectors (pure; the scripts run these).
# --------------------------------------------------------------------------- #

def build_luks_format_argv(device: str,
                           params: Optional[Argon2idParams] = None) -> List[str]:
    """argv for ``cryptsetup luksFormat`` with an explicit argon2id KDF.

    The passphrase is fed on stdin by the caller (``--key-file -``), never on
    the command line."""
    a = params or Argon2idParams()
    return [
        "cryptsetup", "luksFormat",
        "--type", "luks2",
        "--cipher", "aes-xts-plain64",
        "--key-size", "512",
        "--hash", "sha256",
        "--pbkdf", "argon2id",
        "--pbkdf-memory", str(a.memory_kib),
        "--pbkdf-parallel", str(a.parallelism),
        "--iter-time", str(a.iter_time_ms),
        "--batch-mode",
        "--key-file", "-",
        device,
    ]


def build_luks_open_argv(device: str, mapper_name: str) -> List[str]:
    """argv for ``cryptsetup open`` (passphrase on stdin via --key-file -)."""
    if not mapper_name.replace("_", "").isalnum():
        raise ValueError(f"unsafe mapper name: {mapper_name!r}")
    return ["cryptsetup", "open", "--type", "luks2",
            "--key-file", "-", device, mapper_name]


# --------------------------------------------------------------------------- #
# Tiny CLI so the shell scripts share one migration code path.
#   python3 -m provisioning.vault plan
#   python3 -m provisioning.vault apply-migration     (vault must be mounted)
#   python3 -m provisioning.vault revert-migration    (vault must be mounted)
# --------------------------------------------------------------------------- #

def _main(argv: Optional[List[str]] = None) -> int:
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    home = os.path.expanduser("~")
    steps = plan_migration(home)
    cmd = argv[0] if argv else "plan"
    if cmd == "plan":
        for s in steps:
            print(f"{s.source}  ->  {s.vault_target}")
        return 0
    if cmd == "apply-migration":
        for p in apply_plan(steps):
            print(f"migrated {p}")
        return 0
    if cmd == "revert-migration":
        for p in revert_plan(steps):
            print(f"restored {p}")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())
