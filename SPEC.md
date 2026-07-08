# Reticulum Node Medic — Specification

A portable Raspberry Pi 5 device for **provisioning, diagnosing, repairing and
monitoring** Reticulum mesh nodes. Think of it as an OBD scanner, but for a
Reticulum mesh.

- **Hardware:** Raspberry Pi 5, 5-inch touchscreen (1280×720 landscape), Anker
  Prime 26K power bank.
- **Connects to nodes** via USB-C serial or SSH over the network.
- **Operator:** Suga (GitHub `5ugAv`), Melbourne — building a community mesh on
  Heltec WiFi LoRa32 V4 boards and Raspberry Pi nodes.

This project is the **tool**, kept entirely separate from the node firmware
(RNode / RTNode-2400).

## Node types

- **Type A — Raspberry Pi transport node.** Pi 3A+, Zero 2W, or Pi 5 running
  `rnsd` + `lxmd` in transport mode with an attached RNode board.
- **Type B — Standalone RTNode-2400.** Heltec WiFi LoRa32 V4 (5ugAv fork,
  microReticulum). No Pi. WiFi + LoRa bridging with a LAN↔WAN boundary.
- **Type C — RNode only.** A supported LoRa32 board flashed with RNode firmware,
  acting as a radio interface for a Pi node.

## Australian deployment defaults (all overridable)

| Parameter | Value |
|---|---|
| Frequency | 915.125 MHz |
| Bandwidth | 125 kHz (BW125) |
| Spreading factor | SF9 |
| Coding rate | CR5 (4/5) |
| TX power | 17 dBm |
| Regulatory basis | Australian LIPD Class Licence — 915 MHz band |

## Five operating modes

1. **Build** — provisions a node. Hardware selected first; pre-filled LoRa
   params (overridable); produces a photographable "birth certificate".
2. **Repair** (entry says "Diagnose") — one "Run full diagnostic" button, live
   progress, results with "Fix all" / individual fixes. Three-level ping:
   L1 serial loopback, L2 mesh ping, L3 announce heard by the tool.
3. **Monitor** — 24/7 dashboard; hexagonal status indicators; battery/solar/
   signal/last-seen; beacons every 2 h and immediately on breach.
4. **Map** — geographic node view, colour-coded status dots.
5. **Clone Tool** — replicates the tool onto a fresh Pi 5 (fresh identity).

## Self-healing tiers (same diagnostic code in all three)

- **Tier 1** — node fixes itself (systemd timer), logs, sends exception beacon.
- **Tier 2** — node beacons; tool connects remotely and repairs, no site visit.
- **Tier 3** — node is silent; physical visit over USB-C serial, full repair
  even with the stack down.

## Architecture

```
node_profile.py        dataclasses / enums (foundation)
transport/connection.py Connection base, SSH / Serial / Emulated, auto-detect
diagnostics/base.py     DiagnosticCheck, Issue, Fix + helpers
diagnostics/*.py        6 modules, 49 checks (1-49); more to 93
workflows/build.py      10 build steps (@build_step)
workflows/repair.py     RepairWorkflow chaining the 6 modules + ProgressEvents
assets/configs/*.conf   4 Reticulum config templates
ui/                     Kivy theme, widgets, screens, app shell
```

### Diagnostic categories (repair order)

Power & hardware → Reticulum software → Radio & firmware → System health →
Network & mesh → Client connectivity.

Every check produces a plain-English description, a severity
(`critical` / `warning` / `info`), and an auto-fix handler where possible.

## Design principles — never compromise

Plain English everywhere · no internet required in the field · hardware
selected first · pre-filled defaults, always overridable · one button runs the
full diagnostic · every colour means something (green / amber / red / grey) ·
Back + Home on every screen except Monitor · safety panel during active
operations · same diagnostic code runs Tier 1/2/3 · test first, implement
second, test again before moving on.

## Testing discipline

TDD every unit: write the test, watch it fail for the right reason, implement,
run the **entire** suite, confirm the new test passes and every previously
green test still passes. The test count only goes up.
