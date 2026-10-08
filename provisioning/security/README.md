# Node Medic — security hardening (sudo scoping + SSH lock-down)

Human-run runbook for the ORIGINAL medic: review the files, then run the apply
scripts **on the medic** over your existing SSH session.

**CLONES are hardened automatically (keeper's policy, 2026-10-08):** every
clone ends with this same hardening — see "Clones" at the end. The scripts take
the app account as a parameter for that (`--user NAME`); with no `--user` they
mean `nodemedic`, the original medic's account, and behave exactly as before.

**STATUS (re-verified live on the medic 2026-07-27): steps 1 and 2 are ALREADY
APPLIED.** Probed read-only from the LAN:
- sudo is **scoped** — a whitelisted read-only command (`sudo -n /usr/bin/ss
  -tlnp`, `dmesg --level=err`) runs passwordless, while a non-whitelisted one
  (`/bin/true`) is refused. So `010-nodemedic` is installed and in force.
- sshd offers **`publickey` only** (`Permission denied (publickey)` when asked
  for no/other auth) — password auth is already off.
- Durability: `/var/lib/cloud/instances/` holds exactly ONE instance-id
  (`rpi-imager-…`, unchanged since imaging), so cloud-init's once-per-instance
  `users-groups` will not re-run and cannot regenerate the blanket grant.
Do NOT assume the "before" state below still exists — re-probe before acting.

Original live-device state this addressed (as first confirmed 2026-07, now
SUPERSEDED by the above):
- `nodemedic ALL=(ALL) NOPASSWD:ALL` — any shell = instant full root.
- `PasswordAuthentication yes`, `PermitRootLogin without-password`, sshd on
  `0.0.0.0:22` + `[::]:22` — password-guessable, exposed on every interface.
- `~nodemedic/.ssh/authorized_keys` **is present** → key-only auth is safe to enable.
- OS: Debian 13 (trixie), Pi 5. Firewall tool present: `nft` (no ufw/iptables).
  `ssh.socket` is disabled (classic `ssh.service`), so a `ListenAddress` drop-in
  would take effect — but the firewall is preferred (the medic roams networks).

## Files

| File | Installs to | Purpose |
|------|-------------|---------|
| `../sudoers.d/nodemedic` | `/etc/sudoers.d/010-nodemedic` (0440) | scoped NOPASSWD whitelist |
| `sshd_config.d/01-nodemedic-hardening.conf` | `/etc/ssh/sshd_config.d/` | key-only auth |
| `nftables/nodemedic-ssh.nft` | `/etc/nftables.d/` | limit tcp/22 to LAN/private |
| `apply_sudoers.sh` / `rollback_sudoers.sh` | — | install/revert sudoers (`--user`, optional `--self-revert MIN`) |
| `render_sudoers.sh` | — | called by apply: puts THIS machine's backlight device and app user into the policy |
| `apply_sshd.sh` / `rollback_sshd.sh` | — | install/revert sshd (`--user`) |
| `apply_firewall.sh` / `rollback_firewall.sh` | — | load/revert firewall |
| `apply_all.sh` | — | clones only: all three under one root supervisor that confirms or rolls back on a verdict from the parent |

Every privileged command the app runs on the medic itself must be granted in
`../sudoers.d/nodemedic` with exact arguments; `tests/test_privileged_commands.py`
finds them in the code and fails on any that is not classified (on the medic,
on a node, on a clone during its clone flow ...) or not granted.

Recommended order: **1) sudoers → 2) sshd → 3) firewall.** Do each, verify, then
move on. Keep your current SSH session open throughout all three.

## 1. Scoped sudo

A malformed sudoers file locks out **all** sudo (needs physical recovery). The
apply script therefore validates a temp copy with `visudo -c` **before** touching
`/etc`, backs up `/etc/sudoers.d`, installs the scoped file, removes the blanket
`NOPASSWD:ALL`, re-validates the whole ruleset (auto-restoring on failure), and
confirms `nodemedic` can no longer run `ALL`.

```bash
# on the medic, from the repo root
sudo bash provisioning/security/apply_sudoers.sh
# smoke-test the app's privileged paths still work, e.g.:
sudo -n /usr/bin/tee /sys/class/backlight/panel_backlight@1/brightness <<<20  # screen dims
sudo -n /usr/bin/systemctl restart rnode-splitter
# rollback if anything the app needs is denied:
sudo bash provisioning/security/rollback_sudoers.sh    # restores newest backup
```

Two promises the policy keeps (2026-10-08), held to the code by
`tests/test_card_mounts.py`:

- **Card mounts live in root-owned folders, are pinned, and never follow the
  card's own links.** Every card the app mounts goes to a folder under
  `/run/nodemedic` (`sd_boot`, `piboot`), which root creates with one exact
  `install -d` rule; `/run` is root's, so the app account cannot put anything
  of its own there. Each mount rule pins the device pattern, the folder and one
  option string, `nosymfollow,nodev,nosuid,noexec`: a link stored on the card
  is never followed, a device file on it opens nothing, and nothing on it runs
  or gains privilege, so what root writes into one of these folders lands on
  the card. `tee`, `touch` and `umount` name exact files and folders.
  `nosymfollow` needs Linux 5.10 and util-linux 2.38 (Debian 12 or later).
  `provisioning/card_mount.py` builds the commands.
- **sudo never remembers the keeper's password for the app account.**
  `Defaults:<user> timestamp_timeout=0` (rendered for the account like the
  grant line): a password typed for it is asked for again every time, so only
  the exact passwordless rules ever run without one.

The code and the policy move together: a medic running code from 2026-10-08 on
needs this policy re-applied (this step, with the password), or its card
mounts are refused.

## 2. Key-only SSH (anti-lockout)

`apply_sshd.sh` **refuses** unless `authorized_keys` exists **and** a fresh
key-only login (`ssh -o BatchMode=yes nodemedic@localhost true`) succeeds. It
validates with `sshd -t`, verifies `sshd -T` reports `passwordauthentication no`
(catching drop-in **ordering** mistakes vs cloud-init), then **reloads** (not
restarts) sshd so your session survives, and **arms a self-revert timer**:
password auth is restored in `REVERT_MIN` (default 10) minutes **unless you
confirm**.

```bash
sudo bash provisioning/security/apply_sshd.sh
#   -> from ANOTHER terminal, prove a fresh key login works:
ssh nodemedic@nodemedic.local
#   -> only if that succeeded, make it permanent:
sudo bash provisioning/security/apply_sshd.sh confirm
# manual revert anytime:
sudo bash provisioning/security/rollback_sshd.sh
```

If you do nothing, the timer reverts you automatically — you cannot get
permanently locked out by this step.

## 3. Firewall (limit port 22 exposure)

`nodemedic-ssh.nft` adds an isolated `inet nodemedic_ssh` table that accepts
`tcp/22` only from loopback, RFC1918 ranges (whatever LAN the medic roams onto),
and the USB-gadget `/29`, and drops it from public sources. It accepts
established/related first, so your session isn't dropped on load. Same
self-revert pattern.

```bash
sudo bash provisioning/security/apply_firewall.sh
#   -> from another machine on the LAN, prove SSH still works, then:
sudo bash provisioning/security/apply_firewall.sh confirm    # loads at boot too
# revert:
sudo bash provisioning/security/rollback_firewall.sh
```

To tighten to a single fixed subnet, edit the three RFC1918 lines in the `.nft`
file (see its header). To bind sshd to one address instead of firewalling,
uncomment a `ListenAddress` in the sshd drop-in.

## Residual risks / follow-ups (for reviewer attention)

1. **New nodes still get `NOPASSWD:ALL`.** `provisioning/link.py::bootstrap_access`
   writes `<user> ALL=(ALL) NOPASSWD:ALL` to nodes the medic **provisions** (its
   `sudo -S bash -c` is a one-time, password-authenticated bootstrap — not a
   medic runtime path, which is why the test allowlists it). The scoped file here
   is a ready template to install on those nodes too; updating `link.py` to ship
   it is the recommended next step (kept out of this pass to avoid changing the
   provisioning flow untested).
2. **`dd` / `mount` in `NM_IMAGING` are inherently powerful.** They are pinned to
   `of=/dev/sd[a-z]` and to root-owned mount folders with pinned options, but
   raw block-device writes can't be fully constrained by sudoers. The real
   guard is `pi_imager.is_safe_target()` /
   `sd_edit.medic_root_disk()` in Python (both refuse the medic's own disk). If
   you want defence-in-depth, wrap these in a root helper that re-checks the
   target and whitelist only the helper.
3. **Vault mount** (`scripts/vault_mount.sh`) is intentionally **not** in the
   passwordless whitelist — it is human/service-run with an interactive `sudo`.
   If the touchscreen unlock (`ui/vault_unlock.py`, currently a stub) is wired to
   invoke it, add a single `NM_VAULT` alias for the exact helper path then.
4. **Provisioning-of-remote-node commands** (build.py service writes,
   hostnamectl, dpkg/log2ram, clone.py, gadget/uart over an SSH connection) run
   against the **target** node's sudoers, not the medic's, so they are out of
   scope for this file. They were refactored off `sudo bash -c` regardless.

## Clones (automatic, 2026-10-08)

The keeper's policy: **every clone ends with this hardening** — scoped sudo for
the clone's own app account (`pi`), key-only SSH, the SSH firewall. A clone for
the keeper's own fleet keeps the parent medic's key so the keeper can look after
it; a clone for a **new community** has the parent's key removed as the very last
action, leaving no authorized key at all (nobody can log in remotely; its own
screen is unaffected). `workflows/clone.py` does it in its last two steps,
`harden_new_medic` and `remove_parent_key`.

Why a supervisor (`apply_all.sh`) and not the three scripts in the runbook
order: confirming the SSH and firewall changes needs root, and the scoped
sudoers takes root away from the app account — so after the "a non-whitelisted
`sudo -n` is refused" check, the parent could never send `... confirm`. Instead:

1. The parent copies this kit (and the policy, as `sudoers.nodemedic`) into
   `/usr/local/lib/nodemedic/security/`, **root-owned** — the supervisor later
   runs confirm/rollback as root and must never run a file the app account
   could have edited.
2. It starts `apply_all.sh --user pi` as a root unit of its own (`systemd-run`),
   while the clone still has its card's full sudo. The supervisor applies sshd
   (`--caller-proved-key-login`: the parent's own fresh BatchMode login is the
   proof — a clone's own key is not in its authorized_keys, so the loopback test
   cannot pass there), then the firewall, then the scoped sudoers
   (`--self-revert`). Each keeps its own self-revert armed, set to outlast the
   supervisor's window.
3. The parent checks from outside, every check a NEW `ssh -o BatchMode=yes`
   login: it gets back in; `sudo -n /usr/bin/ss -tlnp` (whitelisted) runs;
   `sudo -n /usr/bin/true` (granted by nothing) is refused; the sshd drop-in is
   in place.
4. Only then does it write the verdict `~pi/.nodemedic-harden-confirm` — a plain
   file the scoped account can still create — and the supervisor runs the three
   `confirm`s as root. Any problem: `~pi/.nodemedic-harden-rollback`, and the
   supervisor runs the three rollback scripts. Any failure inside (an apply, a
   confirm) rolls back all three. No verdict within the window: it rolls back
   by itself. If the supervisor dies, each script's own self-revert fires at the
   next boot or its deadline.
5. Status for the parent: `/run/nodemedic-harden/status`; full output:
   `/var/log/nodemedic-harden.log`.

The keeper can still use `sudo` on a clone **with the written-down password**
(the account stays in the `sudo` group; only the passwordless grant is scoped)
— typed each time, since sudo never remembers it for the app account.
