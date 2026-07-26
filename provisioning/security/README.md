# Node Medic — security hardening (sudo scoping + SSH lock-down)

Human-run runbook. **Nothing here is applied automatically.** Review the files,
then run the apply scripts **on the medic** over your existing SSH session.

Live-device state this addresses (confirmed 2026-07):
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
| `apply_sudoers.sh` / `rollback_sudoers.sh` | — | install/revert sudoers |
| `apply_sshd.sh` / `rollback_sshd.sh` | — | install/revert sshd |
| `apply_firewall.sh` / `rollback_firewall.sh` | — | load/revert firewall |

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
   `of=/dev/*` / fixed mountpoints, but raw block-device writes can't be fully
   constrained by sudoers. The real guard is `pi_imager.is_safe_target()` /
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
