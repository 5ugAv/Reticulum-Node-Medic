# Reader-free SD imaging — write the card while it stays in the Pi

Goal (operator, 2026-08-27): bypass the medic's own SD card reader — image the
Pi's SD card while it's still installed in the Pi, over a USB cable. Synthesised
from two overnight investigations (per-Pi hardware mechanism + codebase audit).

## Headline: it already exists, and the core needs no changes

`provisioning/pi_usbboot.py` already does exactly this and is BENCH-PROVEN (Pi
Zero 2 W, 2026-08-01/02): `rpiboot` pushes a tiny RAM payload, the Pi presents
its own SD slot to the medic as an ordinary `/dev/sdX`, and the **entire
existing write/prepare/safety stack images it unchanged** — a gadget-presented
SD enumerates exactly like a card in the reader. It's still wired as the
automatic fallback (`pi_imager_screen._offer_pi_as_reader`) when no reader card
is found. It was DEMOTED from the primary birth path on 2026-08-06 (not deleted)
because reader-free is **not uniform across the Pi fleet** — see the per-model
table. So this is *revive-and-gate*, not *build*.

## The one hardware fact that decides everything

USB **device** boot (the Pi presenting its SD to the medic) needs the SoC's
**OTG-ID pin floating**; USB **host** mode ties it to 0 V. That pin — plus
whether the model has a software-writable boot EEPROM — determines feasibility.

## Per-Pi verdict

| Model | Reader-free on a BLANK card? | How | One-time cost |
|---|---|---|---|
| **Pi Zero 2 W** | **YES — zero touch, NOW** | blank card + the **micro-USB DATA** port (not PWR) to the medic. Proven in repo. | none |
| **Pi 5** | **YES** | hold power button + USB-C, **or** a baked EEPROM `BOOT_ORDER` with RPIBOOT → auto-enters on cable | bake `BOOT_ORDER` once (medic can write it onto the first reader-made card for free) |
| **Pi 4 B** | **YES** (not on factory EEPROM — it has no RPIBOOT rung) | same as Pi 5: baked `BOOT_ORDER` RPIBOOT-last | same one-time EEPROM bake |
| **Pi 3 A+** | **NO** — OTG-ID hardwired to 0 V (host-only). Verified live it never appears on the medic. | (only via a RAM-helper OS + a power-cut A↔A cable — fiddly) | keep on the reader |
| **Pi 3 B+** | **NO — impossible over USB** — onboard USB hub (LAN7515) blocks all device/gadget mode | Ethernet netboot only (the HAWKEYE umbilical), never USB | keep on the reader |

## The elegant unification (Pi 5 / Pi 4)

Because the medic writes the FIRST card in its own reader anyway, it can drop an
EEPROM config (`BOOT_ORDER` with the RPIBOOT rung) onto that boot partition at
the same time. After first boot the Pi **self-presents its SD on cable whenever
the card is dead — zero physical action**. This is the SAME bake the clone
ladder already does for medics (`0xf321`, `workflows/clone.py`), and it also
solves the sealed-case Pi 5 recovery (the HAWKEYE button problem) in one move.

## The "already-imaged card" caveat (important)

rpiboot only fires on a **blank/unbootable** card. If the SD already has a
working OS, the Pi wins the boot race and comes up as a running node (GADGET),
not mass-storage — so you can't just re-image an in-place bootable card via
rpiboot. Options if in-place RE-imaging matters: zero the card's first sector
over the cable to the booted node first (then power-cycle into rpiboot), or a
RAM-helper. For a FRESH birth (blank card in the Pi) none of that applies — it
just works on the capable models.

## What must be built to revive it safely (the 3 reasons it was retired)

1. **A board-capability gate — the load-bearing fix.** The 3A+ and 3B+ CANNOT do
   reader-free and previously failed SILENTLY (identical to a bad cable — cost a
   bench night). Revival MUST detect the model (the flow already knows `_pi_key`)
   and redirect these two to the reader LOUDLY, before any attempt. `pi_usbboot`
   already refuses to guess a BCM283x photo for the same reason.
2. **Loud failure, not silent.** Use `provisioning/plugged_in.py` (distinguishes
   "nothing appeared" from "appeared, no link") so a stuck attempt says why.
3. **Only if in-place re-imaging is in scope:** a way to knock a bootable Pi into
   mass-storage (zero-first-sector-over-cable, or RAM helper). Not needed for
   fresh births.

## Safety: never dd the medic's own disk

Already handled, two independent layers, reusable as-is: `pi_imager.system_disk()`
excludes the `/`-holding device; `list_target_disks()` includes only
removable/USB `/dev/sdX`; `is_safe_target()` (Python) + `assert_safe_target()`
(root helper) both re-check. Harden further by requiring the mass-storage USB id
**`0a5c:0001`** ("the Pi is presenting its SD") for the claimed disk. Residual
gap to close: the destructive `dd` is issued by an unprivileged shell string
guarded only Python-side — worth moving that guard into the root helper for an
unattended field flow.

## Recommended phasing
1. **Pi Zero 2 W — enable reader-free now** (zero-touch, proven): offer "plug the
   Pi in with a blank card" as a first-class route for this model.
2. **Pi 5 / Pi 4 B — bake `BOOT_ORDER` RPIBOOT-last on the first reader-write**,
   then reader-free (and sealed-case recovery) forever after.
3. **Pi 3 A+ / 3 B+ — stay on the reader**, gated loudly. 3B+ recovery = Ethernet.
4. Keep the reader path as the universal fallback — the 2026-08-06 "one uniform
   route" decision was CORRECT; reader-free is a per-model enhancement on top,
   not a replacement.

Files: `provisioning/pi_usbboot.py` (the proven driver), `provisioning/pi_imager.py`
(the write + disk safety), `ui/screens/pi_imager_screen.py` (`_offer_pi_as_reader`,
the live hook), `provisioning/pi_model.py` (model detection for the gate),
`assets/scripts/prepare_card.py` (device-agnostic — no change needed).
