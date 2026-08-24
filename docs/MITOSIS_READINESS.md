# MITOSIS readiness — audited 2026-08-25 night, for tomorrow's build

The operator is printing case v4 (Pi 5 + Heltec Tracker + Waveshare power
HAT). If it fits, tomorrow is MITOSIS — the self-clone. This is the honest
gap map, audited against the code tonight so tomorrow is execution.

## What already EXISTS (verified in-repo tonight)

- **workflows/clone.py** — a real 9-step ladder over an injected connection:
  verify_target_pi5 → transfer_tool → transfer_firmware_cache →
  install_dependencies (from carried wheels) → copy_monitoring_db →
  copy_kin_roster → generate_fresh_identity (B is a NEW identity, never a
  key-copy) → stamp_lineage (parent recorded) → record_child_trust
  (non-transitive, per the trust model).
- **provisioning/direct_link.py** — the WiFi-free medic-to-medic link:
  Pi 5 ↔ Pi 5 over the onboard NIC + ordinary patch cable (auto-MDIX),
  mDNS first, static /29 after first contact installs ETH_LINK_SERVICE.
  Deliberate: no PSK ever written to a FAT partition.
- **ui/screens/mitosis_screen.py** — HONEST stub: "Not wired to a real
  target Pi yet: plain popup, don't fake a clone." (161 lines.)
- **Wheelhouse on the medic**: 17 aarch64 wheels incl. Kivy 2.3.1
  (bundled SDL2 — no apt needed), cryptography, cffi. `~/pi_os_lite.img.xz`
  carried; 36 GB free on the medic's card.
- Trust machinery: MITOSIS's stamp_lineage/record_child_trust already mesh
  with Settings ▸ Trusted operators (clone-of-clone stays untrusted).

## The GAPS (tomorrow's actual work, in order)

1. **Image B's card from the medic's SD reader** (the one birth route):
   pi_os_lite + hostname + medic's ssh pubkey baked ONTO THE ROOTFS
   (custom.toml/cloud-init are inert on this image — the eternal trap), plus
   the B-plan's write-it-down password UX for B's user.
2. **Wire mitosis_screen → direct_link → CloneWorkflow**: discover B over
   the patch cable (mDNS, then /29), open SSH with the baked key, stream the
   9 steps to the screen exactly like BIRTH streams its build.
3. **The hidden biggest gap — OS state is not the repo.** A medic is repo +
   OS: labwc/Wayland session + start_ui.sh autostart, scoped sudoers, nft
   firewall, SSH key-only, i2c/backlight config. The clone ladder copies the
   repo; it needs an `apply_medic_os_state` step (reuse
   provisioning/security + a session-autostart writer) or B boots to a
   console, not a medic.
4. **Wheelhouse preflight**: a step that proves the carried wheels satisfy
   requirements BEFORE imaging starts (fail early on the bench, not at
   install_dependencies over the cable).
5. **Secrets audit on transfer_tool**: rsync excludes must cover
   ~/.reticulum identities, vault container, trust HMAC key, host keys —
   generate_fresh_identity is pointless if a stray copy rides along. Audit
   the exclude list against the 0600-mode files from the durable-writes work.

## Also unlocked by case v4: the power HAT

monitor/ups.py (INA219 @ 0x43) + voltage-gated safe-shutdown were built
2026-07 and have sat DORMANT (no HAT). The moment the HAT is fitted:
- enable i2c (`dtparam=i2c_arm=on` / raspi-config), `i2cdetect -y 1` → 0x43
- read_ups() live → battery% into the medic's own status
- enable the safe-shutdown service (already written, gated on voltage)
- Self Diagnose: add a power check (ups present/voltage sane)

## Open decisions for the operator (ask before building)

- What hardware is Medic B? (Pi 5 + which screen + SD reader?) The clone
  flow can't be verified end-to-end without a target board.
- Does B get the same 58 GB-class card? transfer_firmware_cache +
  wheelhouse + map tiles need the size known.
- Case v4 fit report — the HAT's INA219 address confirmed 0x43 on THIS unit.
