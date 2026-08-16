# JONESEY — the medic's Heltec Wireless Tracker (RNode + GPS)

The Node Medic's dedicated RNode is a **Heltec Wireless Tracker** named
**JONESEY**. It is the medic's OWN hardware, permanently attached — never a work
board, never a flash target for ordinary jobs. `assert_flashable()` exists
specifically to stop the medic writing to it. It does two
jobs on one USB cable: it is the medic's **LoRa radio** (rnsd's interface to the
mesh) **and** its **GPS receiver** (for the birth-cert location, Triage, and
navigating to nodes). The Raspberry Pi 5 has no GNSS of its own, so this board
provides it.

## How the two jobs share one cable

The Tracker has a single USB serial port, and rnsd wants it exclusively. So a small
**serial splitter** (`monitor/serial_splitter.py`) runs on the Pi:

- it owns the real port,
- presents a **virtual port** that rnsd opens instead (LoRa stays online 100%),
- and skims the GPS frames the firmware injects into the stream, writing the
  latest fix to `~/gps_state.json` for the tool to read.

rnsd never sees the GPS frames; the GPS reader never fights rnsd for the port.
Verified live: rnsd's interface stays *Up* while `gps_state.json` updates at the
same time.

## Hardware — what matters

**GNSS chip: Unicore UC6580** — a dual-frequency (L1 + L5/L2), all-constellation
receiver (GPS, GLONASS, BeiDou, Galileo, QZSS). With a real dual-band antenna it
out-positions a single-band u-blox (e.g. a T-Beam Supreme).

**⚠️ V1.1 power gotcha — the #1 reason people think the GPS is broken.**
On the Wireless Tracker **V1.1**, the GNSS is powered from **GPIO3 (VEXT)**, which
must be driven **HIGH**. If it is low, the UC6580 gets no power and produces no
data at all. The firmware drives GPIO3 HIGH at boot (it is shared with the display
power), so this is handled — but if the GPS ever looks dead, GPIO3 is the first
thing to check. (All boards currently sold are V1.1; a V1.0 powers the GNSS
differently.)

**GNSS UART wiring (verified on hardware):** the ESP32-S3 talks to the UC6580 on
**UART1**, MCU **RX = GPIO33**, **TX = GPIO34**, at **115200 baud**. (Note: an
earlier handover had RX/TX the other way round — that produced *no* NMEA. 33/34 is
correct.)

**Dual-band config:** the firmware sends `$CFGSYS,h35155` at boot — GPS L1+L5,
BeiDou B1I+B2a, GLONASS L1, Galileo E1+E5a, SBAS, QZSS. This set suits
Australia/Asia-Oceania (QZSS augmentation + strong BeiDou coverage).

## Antennas — two sockets, and only ONE of them is wired

The board has **two tiny U.FL sockets**. They are not equivalent, and the
difference is the single most expensive thing to get wrong on this board.

- **LoRa (915 MHz)** — the mesh radio. **Always** attach a 915 MHz antenna here
  before transmitting; transmitting without it can damage the radio.
- **GNSS** — **NOT CONNECTED AS SHIPPED.** It becomes usable only after a
  board modification (below). Until that mod is done, anything plugged in here
  does nothing.

### ⚠️ The GNSS U.FL socket goes nowhere

**As shipped, the UC6580 is routed to the onboard ceramic patch antenna — the
~20 mm white square on the board — and NOT to the external GNSS U.FL socket.**
The socket is present but its 0R link is not populated, so until the link is
moved, nothing plugged in there is electrically connected to the receiver.

This is a **solderable choice, not a permanent limitation.** See "Upgrading to
the external antenna" below.

**How this was established (do not re-derive it, and do not soften it):**

1. **Continuity checked with a multimeter** on the actual board. The external
   GNSS U.FL socket is not connected to the receiver.
2. **Heltec's own FAQ** documents a 0R-link modification to switch the Wireless
   Tracker from the onboard patch to the external socket — the vendor confirming
   the external socket is unconnected as shipped.
   *(URL pending — ask the operator. Do not cite a guessed link for this; it is
   the load-bearing citation for the whole finding.)*
3. **Behaviour matched the electrical finding.** A good external GNSS antenna on
   that socket produced nothing, because it was never in the circuit.

An earlier version of this document told you to plug a dual-band L1/L5 active
antenna into the GNSS socket and silicone it down, **without mentioning the
mod** — so it read as a thing that would work out of the box. It does not. That
omission sent people to mount an antenna that was not in the circuit, and then
blame the sky.

### The mounting rule for the board AS IT IS NOW

**The white ceramic patch must face UP, at the sky, with no metal above it.**

While JONESEY runs on the onboard patch, that is the whole antenna procedure for
GPS. There is nothing to plug in, and no antenna purchase will change it.

Taped inside the medic's housing, the patch saw **nothing** — not a weak fix, no
fix. Metal or a board above it is the same as no sky.

### Upgrading to the external antenna

**The operator has a good-quality dual-band GNSS antenna intended for JONESEY.**
It is the right part; the board is simply not wired to it yet. Using it requires
moving the 0R link so the UC6580 feeds the U.FL socket instead of the patch —
the modification Heltec documents in their own FAQ.

Recorded as **NOT YET DONE** as of 2026-08-16. Two things follow:

- **Until the mod:** patch to sky. The antenna sitting in the drawer is not a
  fallback, and plugging it in proves nothing.
- **After the mod:** the patch is out of circuit. The external antenna is then
  mandatory, not optional — a modded board with nothing plugged in has no GNSS
  antenna at all. Re-verify with a sky test and record the date here.

When it is done: photograph the link before and after, note which side it was
moved to, and put that in this file. The 0R link is a 1 mm part and nobody will
remember which way it went.

**Secure the U.FL plug after the mod.** The board-side U.FL socket is the
fragile end and can work loose in the field; a small dab of neutral-cure
silicone holds it. (This advice was correct in the old document — it was only
attached to a socket that did nothing.)

## Getting a fix

- **Point the ceramic patch at the sky.** Indoors, or with the patch facing a
  wall, the floor, or the inside of a case, it may take a long time to get a
  location or never get one.
- **Measured live on 2026-08-07:** patch to open sky gave **9 satellites and a
  lock in ~75 s**. Turned to face the ground, satellites fell to **0 within a
  minute** — while `has_fix` stayed `1` and the stale position kept being served.
  A fix flag is not proof of a location; see "coasting" below.
- The **first outdoor fix** can take a few minutes (cold start).
- Once it has a fix, the firmware pushes position; `~/gps_state.json` shows
  `has_fix: true` with a `lat`/`lng`, plus a live `sats` count.

## JONESEY's screen — what the animations mean

JONESEY runs a custom firmware build with animations on its onboard display.
They are the fastest read on the mesh the medic has: a one-second glance at the
board answers questions that otherwise need `rnstatus` over SSH.

**Operator-reported, 2026-08-16. NOT yet verified against the firmware source** —
that build lives on the medic at `~/overlay_test/RNode_Firmware/`
(`workflows/rnode_flash.py:194`), not in this repo, so nothing here can confirm
the exact states or their appearance. Recorded now because it exists in one
person's head and nowhere on disk.

The display shows:

- **Reticulum connection** — whether the node is attached to the mesh
- **Signal clarity** — quality, not just presence
- **Transmissions** — the board is sending
- **Received signal** — the board is hearing something
- **Busy airwaves** — congestion; the shared resource is under load
- **Fault** — something is wrong

**To finish this section**, on the medic:
`grep -rn "display\|anim" ~/overlay_test/RNode_Firmware/` — then write down each
state's exact trigger and what it looks like, so a stranger can read the screen
without being told. Airtime is this project's scarcest resource; a board that
shows congestion at a glance is a real diagnostic, and it should not depend on
knowing what the pictures mean.

Related: the RNode RGB LED colour vocabulary is a separate language on a separate
indicator — do not conflate the two.

## Diagnostics

The medic's repair run includes a **GPS (GNSS)** category (`diagnostics/gnss.py`)
that reads `~/gps_state.json` and reports, in plain English:

**Check `gps_frames` FIRST.** It separates two failures that look identical and
want opposite actions:

- **`gps_frames == 0`** — the radio is reporting but has *never* sent a GPS
  frame. **This firmware has no GPS enabled. It is not a sky problem**, and
  sending someone outdoors with it wastes an afternoon on a receiver that was
  never going to answer.
- **No GPS data at all** — the Tracker isn't reporting; check it's plugged in,
  GPIO3 power is on, and the splitter is running. **Do not check the GNSS
  socket — nothing belongs in it.**
- **No fix yet** — powered and reporting, no location; the patch needs open sky.
- **COASTING** — still reports a position while tracking **0 satellites**. The
  most dangerous state, because it looks like an answer. That position may be
  where the medic *was*.
- **Too few satellites** — has a location but the fix may be rough.

`gpsd` **is** installed, at `/usr/sbin` — off the user's `PATH`, so `which gpsd`
comes back empty and looks like it is missing. It isn't.

## Troubleshooting quick reference

| Symptom | Likely cause |
|---|---|
| No NMEA / no GPS data at all | GPIO3 not HIGH, or RX/TX pins swapped |
| Radio reports, `gps_frames: 0` | Firmware has no GPS enabled. **Not** a sky problem |
| Data flowing, `sats: 0`, no fix | Ceramic patch not facing the sky, or metal above it |
| External GNSS antenna connected, still nothing | The 0R link mod has not been done — the socket is not in circuit. Use the patch |
| Mod done, antenna removed, no GNSS at all | Correct: the mod takes the onboard patch OUT of circuit. Reconnect the antenna |
| Position reported, `sats: 0` | **Coasting** on an old lock — that location may be stale |
| Weak/rough fix | Few satellites — more open sky, or metal near the patch |
| No LoRa signal, GPS fine | LoRa antenna on the wrong socket |
| `which gpsd` empty | `gpsd` lives in `/usr/sbin`, off the user `PATH`. It is installed |
