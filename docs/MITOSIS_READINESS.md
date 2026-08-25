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
- **Wheelhouse on the medic**: 19 aarch64 wheels incl. Kivy 2.3.1
  (bundled SDL2 — no apt needed), cryptography, cffi — PLUS smbus2 (the
  INA219/UPS driver) and Pillow, found MISSING by tonight's lazy-import scan
  and fetched while online (2026-08-25; medic runs Python 3.13.5, wheels are
  cp313). The runtime lazy-imports are: RNS, PIL, segno, serial, smbus2 —
  all now carried. `~/pi_os_lite.img.xz` carried; 36 GB free.
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
   the code's imports BEFORE imaging starts (fail early on the bench, not at
   install_dependencies over the cable). Tonight's scan method: grep both
   column-0 AND indented imports (this codebase lazy-imports heavily — the
   first pass missed smbus2/PIL entirely).
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

## Medic 2.0 hardware — DECIDED (operator, 2026-08-25 night)

8 GB Pi 5 + Waveshare UPS Module 3S (3x Samsung INR18650-35E, ~38 Wh ->
~4-7 h runtime; 5V 5A output meets the Pi 5) + Heltec Tracker + 5"
touchscreen (DSI, official-branded ribbon) + active cooler.

UPS facts verified against the Waveshare wiki (2026-08-25):
- 8-pin header (2x4, near BOOT): 1=SCL 2=SDA 3/4=3V3 5/6=GND 7/8=5V.
  Monitoring link to the Pi is THREE wires: SDA->phys 3, SCL->phys 5,
  GND->phys 6. NEVER the 5V pins into GPIO; never tie 3V3 rails.
- First cell insert: a lit per-cell LED = that cell is REVERSED (do not
  charge). No output until the BOOT button wakes the protection chip.
  Charge only with the configured 12.6V 2A supply.
- monitor/ups.py expects bus 1 @ 0x43; if i2cdetect shows 0x41/0x42
  (solder pads), change INA219_ADDR — one line, don't rewire.

## Remaining checks before the clone build

- **5" panel resolution** — the UI is built for 720x1280 portrait. `wlr-randr`
  on the booted target tells it in one line; 800x480-class would make layout
  adaptation a real work item, 720x1280 means zero UI work.
- Card size for B (transfer_firmware_cache + wheelhouse + tiles).
- Case v4 fit report; i2cdetect grid on THIS unit.

## THE FIRSTBORN — the new medic's first walkthrough (operator spec, 2026-08-25)

After the clone, the new medic's FIRST BOOT opens a guided walkthrough
directing the operator to birth the Heltec Tracker that will be PERMANENTLY
attached to it — its own radio + GPS, its Jonesey. "It's, you know, number
one child. We should make a fun animation screen, a big celebration for
that happening."

Design intent:
- First-boot detection: lineage stamped (tool_identity has a parent) AND no
  own-radio registered yet -> the walkthrough offers itself.
- Steps, wizard-style with the existing visual language: welcome-as-new-medic
  -> "plug in the Tracker that will live inside this medic" (animation +
  board poll + the green ripple) -> flash via the PROVEN Tracker-RNode path
  (Jonesey's exact build, cb/ca provision) -> provision + register as the
  medic's OWN protected radio (flash-guard engages, same as Jonesey) ->
  **CELEBRATION SCREEN**: the firstborn moment, big and fun — family-line
  motif (parent -> child -> its first child), PIL-preview the animation
  before deploying (standing method).
- The walkthrough ships in the tool tree so every clone carries it; it can
  also be pushed to an already-cloned medic over SSH (rsync), which
  incidentally proves the parent->child update path.
