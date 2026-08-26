# Hardware auto-recognition — plan

Goal (operator, 2026-08-27): make Node Medic recognise every piece of hardware
so the keeper never has to pick their board or Pi from a picture. Synthesised
from three overnight investigations (code audit + radio-board research + Pi
research). Verdict up front: **most of it is already automatic or can be made
automatic; a small, nameable residue is genuinely impossible over USB and gets
a one-glance tell + a learn-once memory instead of a picture-grid guess.**

## Where the user is still asked to identify hardware (the audit)

Brick/safety confirm gates (deliberate, keep): `birth_screen._confirm_board_gate`
/ `_confirm_rnode_board_gate`, `birth_guide._render_confirm_pair`.
Identity *choices* to eliminate: `_render_pick_board`/`_add_rnode_board_pick`
(radio, ambiguity-only), `_render_pick_pi`/`_choose_pi` (Pi, **always asked**),
`_add_rtnode_confirm` (V3/V4/Supreme, ambiguity-only).

## Already automatic today — protect these
- **V3 vs V4**: resolved by USB port-type (V3 = CP2102 bridge → ttyUSB; V4 =
  native 303a → ttyACM). The chooser is correctly skipped when identified.
- **RAK4631, Heltec T114**: self-name in their USB product string.
- **Fresh XIAO**: self-names `seeed-xiao-s3` (lost after our flash).
- **LoRa32 v2.1**: unique `ESP32-PICO-D4` chip package.
- **Per-chip board memory** (`ui/board_memory.py`): the eFuse MAC remembers the
  operator's prior answer for that exact chip — "learn once, never ask again".

## Make automatic — safe, no hardware needed
1. **Wire board-memory into the birth_screen confirm gates.** VERIFIED:
   `birth_guide_screen` records the MAC→board answer on confirm (line ~2583),
   but **`birth_screen`'s brick gates (`_confirm_board_gate`,
   `_confirm_rnode_board_gate`) do NOT** — so confirming a board there teaches
   the medic nothing and it asks again next time. `self._detected["mac"]` is
   available at both gates, so the fix is small. **Safety caveat that makes this
   verify-and-test, not blind:** board-memory keys are DETECT keys (e.g.
   `heltec32_v4`); the RNode gate has the board object (`board.key`, clean), but
   the RTNode gate holds a *target* key (e.g. `heltec_v4`) that must be mapped
   back to the detect key first — a wrong mapping would mis-remember and later
   AUTO-pick the wrong board (a brick risk). So: RNode gate is the clean quick
   win; RTNode gate needs the reverse of `_DETECT_KEY_TO_TARGET` and a test.
   *(code + tests; the RTNode half reviewed before shipping)*

2. **Pi: stop asking — defer the model until after boot.** The `_render_pick_pi`
   trap exists only because the Pi is off/unplugged at card-write time, so its
   model is unknowable then. But almost nothing on the card is model-specific:
   only `dtoverlay=dwc2,dr_mode=peripheral`, which is SAFE on every cable-capable
   Pi (Zero 2 W / 3A+ / 4B / 5). So:
   - write ONE **model-agnostic card** (dwc2 peripheral in config.txt),
   - boot the Pi on the cable, then read `/proc/cpuinfo` Revision (bits 4–11 =
     board type) exactly as `clone.py:52` already does → the medic now KNOWS the
     model and shows the right power/photo/standalone hints itself.
   This turns the six-way "which Pi?" guess into an after-boot reading. *(a real
   change to the card write + provisioning — design-review with operator first)*
   - **Two things still can't be deferred:** (a) the **Pi 3 B+** physically
     can't do the cable link (built-in hub → host-only), so ONE pre-write yes/no
     must catch it before the 4-min write ("Does it have Ethernet + 4 USB-A and
     NO USB-C?"); (b) the **5A-vs-3A supply** split is about the wall adapter,
     not the board — ask it, or read `get_throttled` undervoltage post-boot.

## Make automatic — needs one bench pass (measurements the medic reads itself)
3. **Fill in `_FLASH_SIZE` for every ESP32-S3 board.** This is the single
   highest-leverage lever for the S3-native lookalike group (V4 16MB, XIAO 8MB
   known; T3S3 / T-Beam Supreme / T-Deck / Wireless Tracker unmeasured). Each
   measured board the medic can then auto-exclude. Plug each S3 board in once and
   record esptool's detected flash size → add to `_FLASH_SIZE`. *(bench: plug
   each board, I read the size via the medic and add it)*

## Irreducible over USB — replace the picture-grid with ONE tell + memory
These share silicon identity and CANNOT be told apart over USB. Honest fix: a
single discriminating close-up (`board_images.HOW_TO_TELL`, verified markings
only) + learn-once memory — not a guess from a grid.
- **Classic-ESP32 quad** (LoRa32 v2.0/v1.0, Heltec V2, T-Beam): all plain
  `esp32` behind a bridge. Tells — **T-Beam**: 18650 holder + GPS + AXP192 (huge,
  unmistakable; a firmware I2C scan @0x34 could even confirm post-flash);
  **Heltec V2** silk "WiFi LoRa 32 V2" vs **LilyGO T3** silk revision.
- **S3-native lookalikes not caught by flash-size**: **T-Deck** = QWERTY
  keyboard+trackball; **T-Beam Supreme** = 18650+GPS+SD; **XIAO** = thumbnail
  size + castellated pads; **Wireless Tracker** = colour TFT+GPS.
- **Stock T-Echo** presents as generic `nRF52840 DK` — inferred as the only
  generic nRF in the catalogue (fragile; e-paper + rounded PCB is its tell). The
  flow already offers "double-tap RESET so the bootloader names it".

## What needs the operator
- **Photos**: the S3-native/classic tells above, plus missing board photos
  (lora32_v20, lora32_v10, t3s3) and Pi photos (pi_zero_2w, pi_3b_plus). Only
  verified markings per the repo's own rule — you shoot them, I wire them.
- **Design nod** on the Pi defer-until-boot change (it rewrites the card-write
  path for the sacred birth flow) and on the pre-write 3B+ guard wording.

## Net effect
Radio: 6 boards already clean; flash-size measurements + board-memory take most
of the rest; ~2 small clusters keep a one-glance tell. Pi: the six-way trap
collapses to "write agnostic → boot → read it", with one 3B+ guard + the supply
question. The keeper stops identifying hardware almost everywhere.
