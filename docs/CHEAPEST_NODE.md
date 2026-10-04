# The cheapest working node

For communities with very limited funds. Every figure here is either a real
listing (dated) or arithmetic you can check.

**Bottom line: about AU$7 (US$4.50) per node, and the antenna is free.**

---

## The parts

* ESP32 DevKit, CP2102 or CH340, ESP-WROOM-32  -AU$2.91-  https://www.aliexpress.com/item/1005006697863282.html
    * the classic ESP32 the firmware needs — NOT C3, C6, S2 or S3
    * has onboard USB, so the medic can flash it with just a cable
* RFM95 / SX1276 LoRa module, 868/915 MHz  -AU$3.36-  https://www.aliexpress.com/item/32811523237.html
    * ⚠ select the 868/915 variant — the same listing sells 433 MHz parts
* Wire for six connections  -~AU$0.50-  any hookup wire, or salvaged from anything
* Antenna  -FREE-  8.2 cm of wire (see below)

**Total: ~AU$6.80 per node.** Buy ten of each and it drops further — AliExpress
tiered pricing typically takes 20–30% off at quantity, so budget ~AU$5 a node
at ten-up.

## The antenna is free, and it is not a compromise

A quarter-wave monopole at 915.125 MHz is **8.2 cm** of wire (7.8 cm if you
trim for wire's velocity factor). Solder it to the module's antenna pad, and
solder a second 8.2 cm wire to the module's ground pad pointing the opposite
way — that makes a dipole, which works far better than a lone wire.

**This is not a poor-man's substitute.** On this bench on 2026-08-27 we
measured seven antennas against each other, and the best ear — a decibel ahead
of a 40 cm whip, tied with the best bought whip — was an **8 cm stub**, because
8 cm *is* the right length for this band. A wire cut to the same length is the same antenna.
See `docs/ANTENNA_BENCH_2026-08-27.md`.

    antenna wire   ────────── 8.2 cm ──────────  → to the module's ANT pad
    counterpoise   ────────── 8.2 cm ──────────  → to a GND pad, opposite direction

Keep the wire straight, keep metal away from it, and point it up.

## The wiring — six connections

    ESP32            RFM95 / SX1276 module
    ─────            ─────────────────────
    GPIO 4    ────→  NSS  (chip select)
    GPIO 33   ────→  RST  (reset)
    GPIO 39   ────→  DIO0
    GPIO 18   ────→  SCK
    GPIO 19   ────→  MISO
    GPIO 23   ────→  MOSI
    3V3       ────→  3.3V     ⚠ 3.3 V ONLY — 5 V destroys the radio
    GND       ────→  GND

The cheap modules are **bare castellated** — the pads are ~2 mm apart on the
edge of the board, so wires are soldered directly to them rather than plugged
into pins. Fiddly but entirely doable with a fine tip.

## Flashing it

The Node Medic already carries the firmware offline — `rnode_firmware_esp32_generic.zip`
is in its cache, so **no internet is needed at the point of use**. Plug the
board in, birth it as an RNode, and provision it as a homebrew board.

⚠ The homebrew profile caps transmit power at 17 dBm on `MODEL_FE`
(`MODEL_FF` caps at 14) — pick the right one or the radio stays quiet. This
bit us before; see `rgb-custom-firmware-needs-homebrew-provision` in the
project notes.

## Power

* a USB power bank, or any 5 V USB supply — the devkit regulates to 3.3 V
* for a permanent node: a small solar panel + a USB power bank works, though a
  proper charge controller lasts longer
* the ESP32 is not a low-power design; for battery-only nodes the purpose-built
  boards (nRF52 + SX1262) last far longer. This build is for **mains, power
  bank, or solar** siting.

## What you get for AU$7

A full Reticulum node: it routes, it stores and forwards, it can be reached by
any other node on the mesh. It is not worse at *networking* than a $60 board —
what you give up is battery life, a screen, a case, and GPS.

## What you cannot skip

* **The antenna.** Transmitting with no antenna damages the radio. Solder the
  wire before you power it.
* **The band.** 915 MHz for Australia/NZ/US, 868 for Europe. A 433 MHz module
  will not talk to a 915 network — and looks identical.
* **3.3 V.** Never 5 V to the radio.

## Scaling up for a community

* **Buy in tens.** Postage dominates at these prices, and AliExpress charges
  postage per *store* — put the boards and the radios from the SAME store in
  one cart.
* **One medic serves everyone.** The tool flashes, births, and repairs every
  node; a community needs one medic and as many cheap nodes as it likes.
* **Two nodes make a link; three make a network.** Every node added extends
  the reach of all the others.

---

*Costs read 31 August 2026. AliExpress promotional prices move daily.*
