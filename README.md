# Reticulum-Node-Medic

A portable field tool for **provisioning, diagnosing, repairing and monitoring**
[Reticulum](https://reticulum.network) mesh nodes — an OBD scanner, but for a
Reticulum mesh.

It runs on a Raspberry Pi 5 with a 5-inch touchscreen, powered from a battery
bank, and connects to nodes over USB-C serial or SSH. Built for a community
LoRa mesh on Heltec WiFi LoRa32 V4 boards and Raspberry Pi nodes, with no
internet required in the field — all assets are carried locally.

> This is the **tool**. It is kept entirely separate from the node firmware
> (RNode / the RTNode-2400 5ugAv fork) that it inspects and repairs.

## Six operating modes

1. **VITALS** 🫀 — 24/7 network monitor dashboard with hexagonal status
   indicators, health beacons every 2 h (and immediately on breach), and
   on-demand health polling.
2. **SCAN** 🧫 — network topology and geographic map view, colour-coded status.
3. **BIRTH** 🥚 — provisions a new node from bare hardware. Hardware selected
   first, pre-filled LoRa parameters (all overridable), ends with a
   photographable "birth certificate".
4. **TRIAGE** 🩺 — site assessment and antenna optimisation: a live thermal
   bullseye scores signal clarity / headroom / noise while placing a node.
5. **PROBE** 🩻 — diagnoses and repairs a broken node: one button runs the full
   93-point diagnostic with live progress, then offers "Fix all" or individual
   fixes. Three-level ping: L1 serial loopback → L2 mesh ping → L3 announce
   heard by the tool.
6. **MITOSIS** 🧬 — replicates the tool onto a fresh Pi 5 (with a fresh
   Reticulum identity).

## Node types

- **Type A — Raspberry Pi transport node** (`rnsd` + `lxmd`, attached RNode).
- **Type B — Standalone RTNode-2400** (Heltec V4, 5ugAv microReticulum fork; no
  Pi; health via RNS announce beacons — see below).
- **Type C — RNode only** (a LoRa32 board as a Pi node's radio interface).

## Australian deployment defaults (all overridable)

| Frequency | Bandwidth | SF | Coding rate | TX power |
|---|---|---|---|---|
| 915.125 MHz | 125 kHz | SF9 | 4/5 (CR5) | 17 dBm |

Regulatory basis: Australian LIPD Class Licence, 915 MHz band.

## Architecture

```
node_profile.py          dataclasses / enums (foundation)
transport/connection.py  Connection base, SSH / Serial / Emulated, auto-detect
diagnostics/             base + 7 modules — 91 checks (Pi + RTNode-2400)
workflows/               build (10 steps), repair (chains 6 modules), warnings
monitor/                 health-beacon codec + on-demand poll (Type B)
ui/                      Kivy theme, widgets, screens, app shell, safety panel
assets/configs/          4 Reticulum config templates
assets/scripts/          Heltec V4 NeoPixel patch
```

Diagnostic categories for Pi nodes (repair order): Power & hardware → Reticulum
software → Radio & firmware → System health → Network & mesh → Client
connectivity. Plus a standalone RTNode-2400 module (see below). 91 diagnostic
checks + 4 build-mode cautions, each with a plain-English description, a
severity and — where possible — an auto-fix. The **same** Pi diagnostic code
runs in all three self-healing tiers (on-node systemd timer / remote / physical
serial).

The RTNode-2400 module is **beacon-driven**: those boards have no text console,
so on a physical visit the tool captures the passive serial `[HealthBeacon]`
line, decodes it with the shared `health_beacon` codec, and derives its checks
from the decoded fields (plus a boot-log FATAL scan) — the same wire contract
used over the mesh.

## Type B health beacons

RTNode-2400 nodes can't run LXMF (embedded C++ Reticulum is core RNS only), so
they carry health in the `app_data` of a periodic RNS **announce** on the
`rtnode.health` aspect — a compact 14-byte, big-endian payload decoded by
[`monitor/health_beacon.py`](monitor/health_beacon.py). An **on-demand poll**
([`monitor/health_poll.py`](monitor/health_poll.py)) sends a 1-byte request
(`0x01`) to the node's destination; the node replies with an immediate beacon,
and a clean reply clears a node's warning back to green.

## Development

Test-first throughout. The whole tested core runs headless with no hardware via
an in-memory `EmulatedConnection`.

```bash
python3 -m pytest        # 268 tests, all green
```

The Kivy UI (`ui/`) is written but needs a display to run; the tested core does
not import it.

```bash
python3 main.py          # launch the touchscreen app (needs Kivy + a display)
python3 main.py --version
```

## Status

Tested core complete: node model, transport, all 93 diagnostics + fixes, build
& repair workflows, config templates, health-beacon codec, on-demand poll, and
the UI design system. Next: wiring the announce handler + Monitor dashboard to a
live Reticulum instance, Map and Clone modes.

## License

MIT — see [LICENSE](LICENSE).
