# Encrypt-at-rest for the Node Medic

> **Superseded (2026-09-03).** The LUKS container described below was never
> deployable: `cryptsetup` is not installed on the medic and is not carried, it
> needs root, and a locked container cannot open itself after a power cut. What
> ships instead is `provisioning/records_vault.py` — per-file AES-256-GCM over
> the records only (never the mesh identity), no root, offline — switched on and
> off from Settings ▸ **Encrypt my records** (`ui/screens/encryption_screen.py`,
> `provisioning/encryption_flow.py`). This file is kept as the design record of
> the abandoned path; its commands are not to be run.

Status of the LUKS design: **abandoned.** Everything below is historical.

## 1. Problem

The medic's SD card is a credential. It holds:

- **Private mesh identity keys** read by external daemons:
  `~/.reticulum/storage/transport_identity` (RNS), `~/.lxmd/identity` (LXMF),
  `~/.reticulum/storage/identities/`. `rnsd` and `lxmd` (systemd services,
  auto-start at boot) read these **directly**, so they must be plaintext
  *while the daemons run*.
- **The medic's own data**: `~/.reticulum-node-medic/` — `kin.json`,
  `registry.json` (a location + activity trail), `certificates/`,
  `beacon_targets.json`, `tool_identity.json`, `node_mode`, `home_profile`.

`provisioning/harden.py` already clamps these to `0600`/`0700`. That is **access
control** — it stops another logged-in user reading them on a *running* system.
It does **nothing** if the card is pulled and read on another machine, where the
attacker is root and file modes are irrelevant. This work adds **encryption at
rest**: on a lost/stolen card the sensitive dirs are ciphertext.

## 2. Threat model

| Threat | Protected? |
|---|---|
| SD card lost / stolen, read offline on another machine | **Yes** — dirs are inside a LUKS2 container; without the passphrase they are AES-XTS ciphertext. |
| Other local user on the running medic | Partially — `harden.py` modes still apply; encryption adds nothing here (the vault is mounted and readable by the owner while unlocked). |
| Attacker with the running, **unlocked** medic in hand | **No** — keys are decrypted and mounted. Physical possession of a powered, unlocked device is out of scope. |
| Coerced-passphrase / rubber-hose | **No** — not a goal. |
| Tampering / evil-maid (attacker writes to the card, returns it) | **No** — LUKS gives confidentiality, not authenticated boot. No TPM on this Pi 5, so no measured boot. |
| Forensic recovery of **pre-encryption** plaintext from the flash | **Partial gap** — enabling the vault on a card that already held plaintext keys does NOT scrub the old copies. `rm`-ing the `.pre-vault.bak` backups (and the original blocks) does **not** securely erase on wear-levelled SD flash; remnants may survive and be recoverable with card forensics. |

What it explicitly does **not** protect: RAM while running, the unencrypted
`/boot/firmware` partition, swap (zram is volatile — fine — but check no disk
swap is added later), or anything after a successful unlock.

**Important — flash remnants:** encrypt-at-rest cleanly protects data written
*after* it is enabled. It cannot guarantee that plaintext identity keys already
written to this SD card (before enabling) are unrecoverable, because flash
wear-levelling means `rm`/overwrite don't reliably reach the old blocks. For a
**clean guarantee**, enable the vault and then **generate fresh mesh identities
inside it** (so the private keys were never written plaintext to the card), or
provision the vault on a **freshly-imaged card** from the start. On an
already-deployed medic, treat the old card as potentially still holding a
recoverable copy of the pre-encryption identity.

## 3. Approach: LUKS2-on-a-file container

A single encrypted **file container** (`~/.nodemedic-vault.img`, 256 MiB) holds
the three sensitive roots. On unlock it is loop-mounted at `~/.nodemedic-vault`,
and the original paths become **symlinks** into it, so `rnsd` / `lxmd` / the app
find their files at exactly the same paths.

```
~/.lxmd                -> ~/.nodemedic-vault/lxmd
~/.reticulum           -> ~/.nodemedic-vault/reticulum
~/.reticulum-node-medic-> ~/.nodemedic-vault/reticulum-node-medic
```

When the vault is **locked**, those symlinks dangle → `rnsd`/`lxmd`/the app
cannot find their keys and do not run. That dangling link **is the gate**: no
unlock ⇒ no plaintext keys ⇒ no mesh, until a human is present. For a field
device that is the desired failure mode.

### Why LUKS-on-a-file, not gocryptfs or fscrypt

| | **LUKS2-on-a-file (chosen)** | gocryptfs | fscrypt |
|---|---|---|---|
| Layer | Kernel dm-crypt, block-level | FUSE userspace daemon | Kernel ext4 native |
| Power-loss resilience | **Strong** — AES-XTS is per-sector stateless; the ext4 journal *inside* the container gives crash consistency. The medic loses power abruptly (SD-corruption history), so this matters most. | Weaker — extra FUSE writeback layer; the userspace daemon can be OOM-killed mid-write. | **Strong** — same ext4 crash semantics, in place. |
| Reversibility | **Trivial** — it's one file. Roll back = mount, copy data back to real paths, `rm` the file. Never touches the root FS layout. | Similar (a directory pair). | **Poor** — needs `tune2fs -O encrypt` on the filesystem; un-encrypting a directory in place is awkward; higher risk of bricking. |
| Setup blast radius | Contained to one file + symlinks. | Contained. | Invasive: modifies the root filesystem feature flags. |
| Maturity / simplicity | dm-crypt is the most battle-tested option; `cryptsetup` is one `apt install`. | Mature but adds FUSE moving parts. | Native but the most fiddly key/keyring management. |
| KDF | **argon2id** built into LUKS2 (memory-hard, GPU-resistant). | scrypt. | Relies on external key handling. |

The medic's two hard constraints — **survive abrupt power loss** and **be
cleanly reversible without bricking the mesh** — both point at a LUKS-on-a-file
container. It is confined to a single file, uses a journalled ext4 inside for
crash consistency, and carries argon2id natively. `cryptsetup` is available in
Raspberry Pi OS (`apt install cryptsetup`).

Container is `dd`-zeroed (not sparse) so a power cut can't expose stale disk
blocks, and mounted `noatime,errors=remount-ro` to cut the unflushed-write
window and fail safe.

## 4. Key management — the open decision (needs human sign-off)

The container is protected by an **argon2id** passphrase KDF (LUKS2). *How the
passphrase reaches the container at boot* is the decision to make:

- **(a) Passphrase typed on the touchscreen — RECOMMENDED.** Nothing secret is
  stored on the device; the key exists only in the operator's head. On boot the
  medic waits at an unlock screen. Protects a lost/stolen card fully. Cost:
  an unattended reboot in the field will **not** rejoin the mesh until a human
  unlocks it. For a personally-carried field instrument this is acceptable and
  arguably correct. **This is what the boot integration below implements.**
- **(b) Keyfile on a removable USB.** The key lives on a stick kept separate
  from the medic. Allows *unattended* boot **only when the stick is present** —
  but if the thief grabs medic + stick together, it protects nothing. Best as an
  optional *second* LUKS keyslot (convenience unlock) layered on top of (a).
  `provisioning.vault.derive_key()` (scrypt) is provided for turning a
  passphrase into such a keyfile.
- **(c) Auto-key stored on the same SD — REJECTED.** If the unlock key sits on
  the same card as the ciphertext, a stolen card carries its own key. Protects
  against nothing. Documented only to be explicitly ruled out.

**Recommendation:** ship **(a)** now; offer **(b)** as an optional extra keyslot
later for operators who need unattended reboot. Never **(c)**.

### Open items for the manager

1. **Confirm unlock method (a)** and accept the "no unlock ⇒ no mesh after an
   unattended reboot" trade-off.
2. **Privilege for the unlock agent.** `/run/systemd/ask-password/` is
   root-only. The touchscreen agent (`ui/vault_unlock.py`) must either run from
   a small root helper or the app gets a scoped `sudoers` entry for
   `vault_mount.sh`. Pick one before wiring boot.
3. **Passphrase recovery.** If the operator forgets it, the data is gone by
   design. Decide whether to add a second LUKS keyslot with an escrow/recovery
   passphrase held offline. (LUKS supports up to 8 keyslots.)
4. **Container size** (256 MiB default) — fine for keys + JSON; confirm.

## 5. How rnsd / lxmd get plaintext access after unlock

They must not start until the keys are present. The gate:

```
nodemedic-vault.service   (oneshot, RemainAfterExit)
     │  ExecStart: systemd-ask-password  →  vault_mount.sh --stdin
     ▼
rnsd.service   After= + Requires= nodemedic-vault.service   (drop-in)
lxmd.service   After= + Requires= nodemedic-vault.service   (drop-in)
               (lxmd already Requires rnsd)
```

At boot `nodemedic-vault.service` raises a passphrase prompt and **blocks**
(`--timeout=0`). `rnsd`/`lxmd`, gated behind it, wait. The touchscreen unlock
screen answers the prompt via the standard systemd password-agent protocol
(`ui/vault_unlock.py` → `answer_pending_ask_password`). Once the vault mounts,
the service goes active and the daemons proceed and read their keys through the
symlinks. On stop/shutdown, `ExecStop` unmounts and re-locks (best-effort).

Full boot wiring is a **follow-up** (privilege item #2 above). The mount /
migrate / unlock logic and the agent glue are built and tested now.

## 6. Files

| Path | Role |
|---|---|
| `provisioning/vault.py` | Pure logic: config, KDF params, migration planning, `apply_plan`/`revert_plan`, cryptsetup argv builders, scrypt `derive_key`, a small CLI. Source of truth for parameters. |
| `ui/vault_unlock.py` | systemd password-agent answerer + a Kivy unlock-screen stub. |
| `scripts/vault_create.sh` | Create the LUKS2 container (argon2id) + ext4. |
| `scripts/vault_migrate.sh` | Move the real key dirs in, back originals up, symlink. |
| `scripts/vault_mount.sh` / `vault_unmount.sh` | Unlock+mount / unmount+lock. |
| `scripts/vault_enable_boot.sh` | Install the boot gate (unit + rnsd/lxmd drop-ins). |
| `scripts/vault_rollback.sh` | Full reversible rollback to plaintext. |
| `scripts/nodemedic-vault.service`, `*-vault-override.conf` | The systemd gate. |
| `tests/test_vault.py` | Unit + integration tests. |

## 7. ENABLE procedure (human-run, on the medic)

> Do this with the medic in hand, screen attached, and a known-good backup of
> the SD (or at least the three key dirs) taken first. Nothing here is run by
> the tooling automatically.

```bash
sudo apt install cryptsetup

cd /home/nodemedic/reticulum-tool

# 1. Create the encrypted container (asks for a NEW passphrase, twice).
sudo bash scripts/vault_create.sh

# 2. Move the real key dirs in. Stops rnsd/lxmd/UI, copies data in, keeps the
#    originals as *.pre-vault.bak (NOT deleted).
sudo bash scripts/vault_migrate.sh

# 3. Bring the mesh back up manually and CONFIRM it works with the vault mounted:
sudo systemctl start rnsd lxmd
rnstatus                      # radio online? identities present?

# 4. Only once verified, install the boot gate:
sudo bash scripts/vault_enable_boot.sh

# 5. Reboot and confirm the unlock prompt appears and the mesh comes up after
#    entering the passphrase.
sudo reboot

# 6. When fully satisfied, delete the plaintext backups:
rm -rf /home/nodemedic/.lxmd.pre-vault.bak \
       /home/nodemedic/.reticulum.pre-vault.bak \
       /home/nodemedic/.reticulum-node-medic.pre-vault.bak
```

## 8. ROLLBACK / recovery procedure (must NOT brick the mesh)

If anything misbehaves, revert to plaintext-at-rest:

```bash
cd /home/nodemedic/reticulum-tool
sudo bash scripts/vault_rollback.sh   # removes the gate, copies data back,
                                       # unmounts+locks, re-hardens perms
sudo reboot
```

`vault_rollback.sh` copies each dir back out of the vault to its real path,
removes the symlinks and the systemd gate, and re-runs `harden_permissions`. The
container file is left on disk (delete it manually when satisfied). Because the
originals were only ever *renamed aside* (`*.pre-vault.bak`) or copied — never
deleted — the mesh identity is recoverable even if a copy step fails.

Emergency manual recovery (if the scripts themselves are unavailable) is just:
`sudo systemctl stop rnsd lxmd`, remove the dangling symlinks, and
`mv ~/.lxmd.pre-vault.bak ~/.lxmd` (etc.), then reboot.

## 9. Tests

`tests/test_vault.py` (runs on Mac/CI, no root, no real device):

- KDF: argon2id params surface in the cryptsetup argv; scrypt `derive_key` is
  32 bytes, deterministic, salt/passphrase-sensitive, rejects short salts.
- Config validation (rejects tiny vault, weak argon2, shell-unsafe mapper name),
  home expansion.
- Migration **planning** covers all three roots, targets inside the mount.
- Migration **integration** against a throwaway temp tree with DUMMY keys:
  `apply_plan` symlinks originals into a stand-in "mount", data stays readable,
  originals preserved as `.pre-vault.bak`, idempotent re-apply, refuses to
  clobber a stale target; `revert_plan` restores real dirs with keys intact.
- Unlock agent: `answer_pending_ask_password` delivers `+<passphrase>` to a real
  temp AF_UNIX datagram socket; returns False with no pending request.

Skipped unless run on the medic as root with `cryptsetup`+`losetup`:
`test_real_luks_container_roundtrip` — creates a tiny **throwaway** LUKS
loopback in a temp dir, formats/opens/mkfs/mounts, round-trips DUMMY data, and
fully self-cleans. Never touches real data or a real device.
```
python3 -m pytest tests/test_vault.py -q
```
