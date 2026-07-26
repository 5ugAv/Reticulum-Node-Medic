# Node Medic sandbox — a software mirror for safe testing

A throwaway Debian 13 (aarch64) VM that reproduces the **software** surface of the
real Node Medic, so risky changes — the sudo-scoping, SSH hardening, firewall, and
the encrypt-at-rest vault — can be tested and broken freely **without ever touching
the live medic**.

## What it faithfully mirrors
- Debian 13 (trixie), aarch64 (same architecture as the Pi 5).
- The `nodemedic` app user, and the **cloud-init blanket sudo** exactly as the real
  medic ships it (`/etc/sudoers.d/90-cloud-init-users`), so scoping — and the
  "cloud-init regenerates it" gotcha — are testable.
- Stub `rnsd` / `lxmd` / `rnode-splitter` systemd services (so `systemctl
  restart/enable/status` and the vault's service-gating behave).
- `sshd`, `nftables`, `cryptsetup`, NetworkManager (`nmcli`/`iw`), `acl`.
- Dummy identity keys + a kin/registry location trail at the **real paths and
  perms** (world-readable keys, `0664` kin.json) — realistic targets for
  `provisioning/harden.py`, the vault migration, and the sudoers portal path.
- The current working tree, copied to `~nodemedic/reticulum-tool`.

## What it does NOT do (needs real hardware)
The LoRa **radio**, the **touchscreen**, **GPS**, and **USB flashing**. For those,
use a real Pi (e.g. a MITOSIS clone).

## Prerequisites
Native arm64 [Lima](https://lima-vm.io) (Apple Virtualization.framework). The
x86_64 Homebrew build is slow here; fetch the arm64 release directly:

```sh
mkdir -p ~/.nodemedic-sandbox-tools && cd ~/.nodemedic-sandbox-tools
VER=$(curl -fsSL https://api.github.com/repos/lima-vm/lima/releases/latest \
      | grep -oE 'v[0-9.]+' | head -1)
curl -fsSL -o lima.tgz \
  "https://github.com/lima-vm/lima/releases/download/${VER}/lima-${VER#v}-Darwin-arm64.tar.gz"
tar xzf lima.tgz          # gives ./bin/limactl
export LIMA_HOME=~/.nodemedic-sandbox-tools/lima-home
alias limactl=~/.nodemedic-sandbox-tools/bin/limactl
```

## Lifecycle
```sh
limactl start  sandbox/nodemedic-sandbox.yaml --name=nodemedic-sandbox   # ~5-15 min first boot
limactl shell  nodemedic-sandbox sudo -iu nodemedic                      # become the app user
limactl stop   nodemedic-sandbox
limactl delete nodemedic-sandbox                                         # throw away, rebuild clean
```

## Testing the risky changes (inside the VM, as `nodemedic`)
```sh
cd ~/reticulum-tool
python3 -m pytest tests/ -q

# Sudo scoping — the apply script validates with visudo before touching /etc,
# removes the blanket, re-validates, and auto-restores on failure.
sudo bash provisioning/security/apply_sudoers.sh
sudo -l -U nodemedic                      # should list ONLY the NM_* commands
sudo -n /usr/bin/tee /sys/class/backlight/panel_backlight@1/brightness </dev/null  # allowed
sudo -n cat /etc/shadow                   # DENIED (proves the scope)

# SSH hardening (self-reverts in 10 min unless you `confirm`).
sudo bash provisioning/security/apply_sshd.sh

# Encrypt-at-rest vault (create -> migrate -> verify -> rollback).
sudo apt-get install -y cryptsetup
sudo bash scripts/vault_create.sh
sudo bash scripts/vault_migrate.sh
sudo bash scripts/vault_rollback.sh
```

If a change bricks the sandbox, `limactl delete` + `start` gives a clean medic in
minutes — which is the whole point.
