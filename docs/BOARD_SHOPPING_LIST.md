# Board shopping list — one of each, to prove on Node Medic

From the four-agent hardware survey, 2026-08-31. Everything here is a board a
Node Medic could plausibly flash as an RNode or RTNode-2400. Ordered so the
money buys the most capability soonest.

Legend — **effort**:
* **FREE** = firmware target already exists; catalogue entry only.
* **ALIAS** = pin-identical to a board we already flash.
* **PORT** = pin map exists (Meshtastic `variants/`), needs a board definition.
* **PROJECT** = needs new firmware capability (a radio driver / MCU port).

Prices are indicative (USD, Aug 2026) and stock moves; treat as a guide.

---

## Tier 0 — buy first: proves the whole pipeline

| Board | MCU / radio | Why | Effort | ~$ |
|---|---|---|---|---|
| **Heltec Wireless Stick Lite V3** | ESP32-S3 / SX1262 | **Pin-identical to the Heltec V3** we already flash. The cheapest possible proof that porting works. | ALIAS | 15 |
| **CDEBYTE EoRa-S3** | ESP32-S3 / SX1262 (or 1268) | Pin-identical to the T3-S3 we flash, **and Ebyte ships where LilyGO does not** — the best board for remote-community supply. | ALIAS/PORT | 20 |
| **Heltec Wireless Paper** | ESP32-S3 / SX1262 | Already supported by RNode CE (0x3F). E-paper = readable in sun, sips power. | FREE | 25 |
| **Generic ESP32 devkit + RFM95W (or Ra-01H) module** | ESP32 / SX1276 | **THE $8 NODE.** Firmware already carried offline. Buy 3 — this is the one a community with no supply chain can actually build. Wiring: CS→4, RST→33, DIO0→39, SCK→18, MISO→19, MOSI→23. | FREE | 8 ea |

## Tier 1 — solar / field nodes (the ones that live outdoors)

| Board | MCU / radio | Why | Effort | ~$ |
|---|---|---|---|---|
| **Seeed SenseCAP Solar Node P1** | nRF52840 / SX1262 | Purpose-built solar, IPX6, **all buttons + USB outside the shell** — the single most reflashable sealed device found. | PORT | 60 |
| **Heltec Mesh Solar** | nRF52840 / SX1262 | Solar-purposed, pin map published. | PORT | 40 |
| **LilyGO T3-S3 V1.3** | ESP32-S3 / SX1262 | **V1.3 adds a real solar input** (4.5–5.6 V barrel, CN3065 charger). Cheapest board in LilyGO's range. ⚠ above 5.6 V a Zener conducts and heats. | PORT | 14–20 |
| **Heltec Mesh Node T1** | nRF52840 / SX1262 | Small sealed node, pin map published. | PORT | 30 |

## Tier 2 — worth having on the bench for coverage

| Board | MCU / radio | Why | Effort | ~$ |
|---|---|---|---|---|
| **Seeed XIAO nRF52840 + Wio-SX1262 kit** | nRF52840 / SX1262 | Tiny, cheap, battery-friendly. ⚠ its variant has **three conflicting pin blocks** (kit revisions) — pick the right one. | PORT | 25 |
| **RAK3312** | ESP32-S3 / SX1262 | WisBlock ecosystem. ⚠ radio rail must be enabled first (`SX126X_POWER_EN`). | PORT | 35 |
| **ELECROW ThinkNode M1** | nRF52840 / SX1262 | External RESET, cased, cheap. ⚠ **TCXO is 3.3 V, not the usual 1.8 V**. | PORT | 30 |
| **LilyGO T-Echo Lite** | nRF52840 / SX1262 | Native USB + UF2 = easy recovery. E-paper. | PORT | 27 |
| **CanaryOne** | nRF52840 / SX1262 | Same Heltec-module layout as Mesh Solar. | PORT | 40 |
| **Adafruit HUZZAH32 + RFM95 FeatherWing** | ESP32 / SX1276 | `BOARD_HUZZAH32` exists in **both** RNode firmwares. DIY tier (three jumpers to solder). | FREE | 30 |

## Tier 3 — buy only to answer a specific question

| Board | Why you'd buy it | Caution |
|---|---|---|
| **B&Q Station G2/G3** | A dedicated external *firmware download* button — effectively unbrickable, the best "sealed device" test case. | ⚠ **Flashes clean on plain USB but the LoRa PA will NOT transmit without 15 V PD.** Silent no-RF. |
| **RAK WisMesh Pocket V3** | Cased consumer device, external button forces DFU — good convert-flow test. | Recovery needs the button; V1/V2 need screws + IPEX. |
| **LilyGO T-Deck Pro** | Keyboard + screen, a "finished product" node. | ⚠ write address is `0`, not `0x1000`; TCXO 2.4 V; `LORA_EN` gate. |
| **Heltec Vision Master E290** | E-paper, LoRa pins identical to Heltec V3. | Display work only. |

## Do NOT buy (for this purpose)

| Board | Reason |
|---|---|
| **Seeed T1000-E**, **Wio Tracker WM1110**, **T-Beam 1W / LR1121 SKUs** | **LR11xx radio — RNode firmware has no driver at all.** A whole project, not a pin map. |
| **Anything SX1280** (T-LoRa v2.1-1.8, MakePython nRF52840) | 2.4 GHz — different band plan entirely. |
| **RadioMaster Bandit / Micro / Nano** | USB flashing unproven (specs show no USB port); **Micro and Nano are indistinguishable by hardware model**. |
| **DFRobot FireBeetle + LoRa Cover** | DFRobot's own page: FireBeetle 2 and FireBeetle "pins and sizes are not compatible" — the advertised pair does not mate. |
| **M5Stack Unit LoRaWAN**, **Waveshare UART LoRa HATs**, **Ebyte `T`-suffix modules** | Closed AT-command firmware — the transceiver is unreachable. |
| **M5Stack C6LoRa / Unit C6L** | ESP32-C6 — no RNode firmware support for that MCU at all. |
| **Heltec MeshPocket**, ThinkNode M3/M6, T1000-A/B | **No usable USB data path** (charge-only / pogo / aviation connector). |
| **LilyGO T-Watch S3 (original)** | Vendor recovery begins "remove the back and extract the battery"; no reset button. |
| **LilyGO T-Halow, T-SIM series** | Not LoRa at all. |
| **goTenna** | Si4460, not Semtech — cannot run this firmware. |

## The one that got away

**Nano G2 Ultra** (nRF52840 / SX1262 @ 22 dBm) is on merit the best board in the
entire survey — **wideband 815–940 MHz, so a single SKU works in every country
on earth**, no band question ever. But the vendor is mid-rebrand
(uniteng.com → bqvoy.com), **every listing is out of stock**, and shipping
excludes all of Africa and Latin America. Revisit if it returns.

## Notes for whoever does the buying

* **Band matters**: buy the **915 MHz** SKU for AU/NZ/US, 868 for EU. Some
  boards ship the radio as a purchase-time choice.
* **The SKU trap**: T-LoRa Pager, T-Watch S3 and T-Watch Ultra ship
  SX1262/SX1280/CC1101/LR1121/SI4432 **on identical PCBs**. USB cannot tell
  them apart — record which radio you bought at purchase time.
* **Antennas**: every board needs one for its band before it transmits. See
  `docs/ANTENNA_BENCH_2026-08-27.md` — the fleet picks are the thick BBREE
  whip and the 8 cm AliExpress stubby.
* **Cables**: buy known **data** USB cables. A charge-only cable is
  indistinguishable from dead firmware and has cost this bench hours.
