# Board shopping list

From the four-agent hardware survey, 2026-08-31. Every board here could be
flashed as an RNode or RTNode-2400. Tick as you buy.

Prices indicative (USD, Aug 2026). Buy the **915 MHz** SKU (AU/NZ/US) or 868 (EU).

---

## Buy first — proves the porting pipeline (~$70)

- [ ] **Heltec Wireless Stick Lite V3** — ~$15
      - pin-identical to the Heltec V3 we already flash (free support, an alias)
- [ ] **CDEBYTE EoRa-S3** — ~$20
      - pin-identical to the T3-S3 we already flash
      - Ebyte ships where LilyGO doesn't — best board for remote supply
- [ ] **Heltec Wireless Paper** — ~$25
      - already supported by RNode CE; e-paper, sun-readable, low power
- [ ] **Generic ESP32 devkit ×3** — ~$5 each
- [ ] **RFM95W or Ra-01H module ×3** — ~$3 each
      - THE $8 NODE. Firmware already carried offline on the medic
      - wiring: CS→4, RST→33, DIO0→39, SCK→18, MISO→19, MOSI→23

## Solar / field nodes

- [ ] **Seeed SenseCAP Solar Node P1** — ~$60
      - most reflashable sealed device found: buttons + USB outside an IPX6 shell
- [ ] **Heltec Mesh Solar** — ~$40
      - solar-purposed nRF52840 + SX1262, pin map published
- [ ] **LilyGO T3-S3 V1.3** — ~$14–20
      - cheapest board in the range AND the only one with real solar input
      - ⚠ above 5.6 V a Zener conducts and heats — keep the panel in spec
- [ ] **Heltec Mesh Node T1** — ~$30
      - small sealed node, pin map published

## Bench coverage

- [ ] **Seeed XIAO nRF52840 + Wio-SX1262 kit** — ~$25
      - ⚠ its variant has three conflicting pin blocks (kit revisions)
- [ ] **RAK3312** — ~$35
      - ⚠ radio rail must be enabled first (SX126X_POWER_EN)
- [ ] **ELECROW ThinkNode M1** — ~$30
      - ⚠ TCXO is 3.3 V, not the usual 1.8 V
- [ ] **LilyGO T-Echo Lite** — ~$27
      - native USB + UF2 = easy recovery; e-paper
- [ ] **CanaryOne** — ~$40
      - same Heltec-module layout as Mesh Solar
- [ ] **Adafruit HUZZAH32 + RFM95 FeatherWing** — ~$30
      - supported in BOTH RNode firmwares; DIY tier (three jumpers to solder)

## Buy only to answer a question

- [ ] **B&Q Station G2/G3**
      - external firmware-download button — effectively unbrickable test case
      - ⚠ flashes clean on plain USB but the LoRa PA will NOT transmit without 15 V PD
- [ ] **RAK WisMesh Pocket V3**
      - cased consumer device, external button forces DFU — convert-flow test
- [ ] **LilyGO T-Deck Pro**
      - ⚠ write address is 0, not 0x1000; TCXO 2.4 V; LORA_EN gate
- [ ] **Heltec Vision Master E290**
      - e-paper; LoRa pins identical to Heltec V3

---

## DO NOT BUY

- **Seeed T1000-E, Wio Tracker WM1110, T-Beam 1W / any LR1121 SKU**
      - LR11xx radio — RNode firmware has no driver at all
- **Anything SX1280** (T-LoRa v2.1-1.8, MakePython nRF52840)
      - 2.4 GHz, different band plan entirely
- **RadioMaster Bandit / Micro / Nano**
      - USB flashing unproven; Micro and Nano are indistinguishable by hardware model
- **DFRobot FireBeetle + LoRa Cover**
      - DFRobot's own page: the two "are not compatible" — the advertised pair doesn't mate
- **M5Stack Unit LoRaWAN, Waveshare UART LoRa HATs, Ebyte T-suffix modules**
      - closed AT-command firmware — the transceiver is unreachable
- **M5Stack C6LoRa / Unit C6L**
      - ESP32-C6 — no RNode firmware support for that MCU
- **Heltec MeshPocket, ThinkNode M3/M6, Seeed T1000-A/B**
      - no usable USB data path (charge-only / pogo / aviation connector)
- **LilyGO T-Watch S3 (original)**
      - vendor recovery starts "remove the back and extract the battery"; no reset button
- **LilyGO T-Halow, T-SIM series** — not LoRa at all
- **goTenna** — Si4460, not Semtech; cannot run this firmware

## The one that got away

- **Nano G2 Ultra** — best board in the survey on merit
      - wideband 815–940 MHz: ONE SKU works in every country, no band question ever
      - but vendor mid-rebrand, every listing out of stock, ships to no part of
        Africa or Latin America. Revisit if stock returns.

## Before you buy

- Check the **band**: 915 MHz for AU/NZ/US, 868 for EU
- **The SKU trap**: T-LoRa Pager, T-Watch S3 and T-Watch Ultra ship
  SX1262/SX1280/CC1101/LR1121/SI4432 on identical PCBs — USB cannot tell them
  apart, so write down which radio you bought
- Every board needs an **antenna** for its band before it transmits
  (see docs/ANTENNA_BENCH_2026-08-27.md — fleet picks: thick BBREE whip, 8 cm stubby)
- Buy known **data** USB cables — a charge-only cable looks exactly like dead
  firmware and has cost this bench hours
