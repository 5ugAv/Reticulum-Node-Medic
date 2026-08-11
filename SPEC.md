# Reticulum Node Medic — Specification

Easy to use Reticulum network **building / monitoring / maintenance** tool,
on a portable Raspberry Pi 5 device.

- **Hardware:** Raspberry Pi 5, 5-inch touchscreen (1280×720 landscape), Anker
  Prime 26K power bank.
- **Connects to nodes** via USB-C serial or SSH over the network.
- **Operator:** Suga (GitHub `5ugAv`), Sampleton — building a community mesh on
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

## Six operating modes (sidebar order)

1. **VITALS** 🫀 — 24/7 monitor dashboard; hexagonal status indicators;
   battery/solar/signal/last-seen; beacons every 2 h and immediately on breach.
2. **SCAN** 🧫 — network topology + geographic node view, colour-coded status.
3. **BIRTH** 🥚 — provisions a node. Hardware selected first; pre-filled LoRa
   params (overridable); produces a photographable "birth certificate".
4. **TRIAGE** 🩺 — site assessment and antenna optimisation (thermal bullseye:
   clarity / headroom / noise, adaptively calibrated on site).
5. **PROBE** 🩻 — one "Run full diagnostic" button, live progress, results with
   "Fix all" / individual fixes. Three-level ping: L1 serial loopback, L2 mesh
   ping, L3 announce heard by the tool.
6. **MITOSIS** 🧬 — replicates the tool onto a fresh Pi 5 (fresh identity).

(Former names: Monitor→VITALS, Map→SCAN, Build→BIRTH, Diagnose/Repair→PROBE,
Clone Tool→MITOSIS.)

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
diagnostics/*.py        7 modules, 91 checks (6 Pi modules + RTNode-2400)
workflows/build.py      10 build steps (@build_step)
workflows/repair.py     RepairWorkflow chaining the 6 Pi modules + ProgressEvents
monitor/*.py            health-beacon codec + on-demand poll (Type B)
assets/configs/*.conf   4 Reticulum config templates
ui/                     Kivy theme, widgets, screens, app shell
```

### Diagnostic categories (Pi nodes, repair order)

Power & hardware → Reticulum software → Radio & firmware → System health →
Network & mesh → Client connectivity.

A seventh module diagnoses standalone RTNode-2400 (Type B) boards from their
serial `[HealthBeacon]` line (no text console exists on those boards).

Every check produces a plain-English description, a severity
(`critical` / `warning` / `info`), and an auto-fix handler where possible.

## Design principles — never compromise

Plain English everywhere · no internet required in the field · hardware
selected first · pre-filled defaults, always overridable · one button runs the
full diagnostic · every colour means something (green / amber / red / grey) ·
Back + Home on every screen except Monitor · safety panel during active
operations · same diagnostic code runs Tier 1/2/3 · test first, implement
second, test again before moving on.

## Say only what you have checked

**Nothing is stated unless it is true, and what is true is what the thing in
front of you reported — now.** Not what its datasheet allows, not what was true
five minutes ago, not what the more likely-sounding of two causes would be.

This is a build rule, not a sentiment. Over 9–11 August 2026 every hard bug on
the bench turned out to be the tool asserting something it had not checked, and
each one cost hours pointed at the wrong suspect:

| The claim | What was actually true |
|---|---|
| "This node has Bluetooth" | Read off the board type. Its adapter was rfkill-blocked and had never been asked. |
| "The USB link wedged" | `NETDEV WATCHDOG` from hours earlier, still in the ring buffer, cited against a build running after the cause was fixed. |
| "The mesh stack is down" | `rnstatus` was not on `PATH`. The mesh was hearing announces at that moment. |
| "USB port handed back" | A `sed` through three parsers returned rc=0 and changed nothing. The node came up blind to its own radio. |
| "Radio did not answer" | Printed on every Pi certificate, for a radio that is attached *after* the build. |
| "Could not read /proc/cpuinfo — is the node reachable?" | It was reachable. ssh's stderr said `Host key verification failed`, and was being discarded. |

**Three of those were introduced while fixing the others.** So the discipline
has to be procedural rather than a matter of care:

1. **Read back every privileged or remote write.** A write that is not read back
   is a claim, not a fact. Prefer transforming in Python and writing the whole
   file with `tee` over a regex crossing `shlex.quote`, `bash -c` and a remote
   shell — three chances to arrive as something else, all of them silent.
2. **Date your evidence.** `dmesg` is a ring buffer, not a statement about now.
   If a reading cannot be timed, it cannot be cited.
3. **A capability is what the thing reported.** Never promote a board's
   datasheet, a roster entry or a node type into a live fact. Keep
   *reported-working*, *reported-down* and *never-mentioned* visibly distinct —
   collapsing the last two is the gap assumptions get poured into.
4. **"I could not check" is its own answer.** Distinct from "it is broken", and
   it must not offer a repair that would not help.
5. **Probe addresses, not names.** A name that can resolve two ways is not an
   address; use the one that answered.
6. **Prefer the operator's own diagnostic.** The radio's screen reads
   `On @ 1.8kbps` with a filled bar when a host has opened it. That needs no
   tools, works on a node across town, and found a bug the tool had been
   misreporting for two days.

When the evidence is missing, say so. A screen that admits it does not know is
worth more than one that guesses well, because the operator has to be able to
believe the warnings that matter.

## Testing discipline

TDD every unit: write the test, watch it fail for the right reason, implement,
run the **entire** suite, confirm the new test passes and every previously
green test still passes. The test count only goes up.
