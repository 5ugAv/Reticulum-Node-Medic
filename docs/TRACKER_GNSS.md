# Heltec Wireless Tracker — RNode + GPS (build & setup notes)

The Node Medic's dedicated RNode is a **Heltec Wireless Tracker**. It does two
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

## Antennas — two sockets, don't mix them up

The board has **two tiny U.FL antenna sockets**:

- **LoRa (915 MHz)** — the mesh radio. **Always** attach a 915 MHz antenna here
  before transmitting; transmitting without it can damage the radio.
- **GNSS** — the GPS. Use a **dual-band L1 + L5 active** antenna to get the
  UC6580's real accuracy. A recommended pairing is an **L1/L2/L5 helical antenna
  with a locking SMA**, via a U.FL → SMA pigtail.

**Silicone the U.FL socket.** The board-side U.FL socket is the fragile part — it
can work loose and fall off in the field. After connecting the GPS antenna, put a
**small dab of neutral-cure silicone** around the socket to hold it. (The SMA end
of the pigtail is robust; it's the U.FL end on the board that needs securing.)

## Getting a fix

- The GPS needs a **clear view of the sky**. Indoors or up against a building it
  may take a long time to get a location, or never get one.
- The **first outdoor fix** can take a few minutes (cold start).
- Once it has a fix, the firmware pushes position; `~/gps_state.json` shows
  `has_fix: true` with a `lat`/`lng`, plus a live `sats` count.

## Diagnostics

The medic's repair run includes a **GPS (GNSS)** category (`diagnostics/gnss.py`)
that reads `~/gps_state.json` and reports, in plain English:

- **No GPS data** — the Tracker isn't reporting; check it's plugged in, the GPS
  antenna is on the GNSS socket, GPIO3 power is on, and the GPS service is running.
- **No fix yet** — powered and reporting, but no location; needs open sky.
- **Too few satellites** — has a location but the fix may be rough.

## Troubleshooting quick reference

| Symptom | Likely cause |
|---|---|
| No NMEA / no GPS data at all | GPIO3 not HIGH, or RX/TX pins swapped |
| Data flowing, `sats: 0`, no fix | No GPS antenna, or no sky view (indoors) |
| Weak/rough fix | Few satellites — more open sky / better antenna placement |
| No LoRa signal, GPS fine | LoRa antenna on the wrong socket (GNSS instead of LoRa) |
