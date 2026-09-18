# Board coverage — what the medic has proven

**Generated from the cert ledger — do not hand-edit.** Edit only `provisioning/board_intent.py`, then regenerate with `python3 scripts/board_coverage.py --write`. DONE is the medic's own certificates; TODO is intent minus done, so it shrinks itself as boards get born (operator, 2026-09-18).

## Proven (from the ledger)

| Board | RNode | RTNode-2400 | Pi+RNode |
|---|---|---|---|
| Ebyte EoRa-S3 | — | ✅ | — |
| Heltec LoRa32 v4 | — | — | ✅ |
| Heltec LoRa32 v4 (RGB NeoPixel) | ✅ | — | — |
| Heltec Mesh Node T114 | ✅ | — | — |
| Heltec Wireless Tracker | ✅ | — | — |
| Heltec32 V3 | — | ✅ | — |
| LilyGO LoRa32 v2.1 (T3 v1.6.1) | ✅ | — | — |
| LilyGO T-Echo | ✅ | — | — |
| RAK4631 | ✅ | — | — |
| Seeed XIAO ESP32S3 (Wio-SX1262) | ✅ | — | — |

## To prove (intent minus done)

| Board | Firmware | Status |
|---|---|---|
| Heltec Mesh Node T114 | rtnode2400 | ⬜ owed — already rnode |
| LilyGO T-Echo | rtnode2400 | ⬜ owed — already rnode |
| Seeed XIAO ESP32S3 (Wio-SX1262) | rtnode2400 | ⬜ owed — already rnode |

## Raspberry Pi host builds

Proven Pi+RNode radio boards: Heltec LoRa32 v4.

Declared host intentions (host verification is a bench check — the ledger records the radio, not always the Pi model):
- **pi_3a_plus** with Heltec LoRa32 v4
- **pi_zero_2w** with Heltec LoRa32 v4
