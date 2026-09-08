# Ebyte EoRa-S3-900TB — RNode firmware port

Five patches against **RNode_Firmware_CE** (baseline `a42f8d3`). Apply in a
clean CE checkout, then `make firmware-eora_s3`.

    patch -p1 < boards.patch      # BOARD_EORA_S3 0x47, PRODUCT 0xD3, MODEL_CF 0xCF, pin map, LEDs
    patch -p1 < utils.patch       # setTxPower dispatch, eeprom_product_valid, model check, LED fns
    patch -p1 < radio.patch       # reset the SX1262 in preInit, and wait on BUSY
    patch -p1 < display.patch     # the OLED: I2C pins, Wire.begin, orientation
    patch -p1 < make.patch        # the firmware-eora_s3 target

## Also builds as an RTNode-2400

Added 2026-09-08. RTNode-2400 shares the RNode_Firmware lineage (`Boards.h`,
`Utilities.h`, `sx126x.cpp`), so the port is the same shape as the one below.

    rtnode-boards.patch          BOARD_EORA_S3 0x47, PRODUCT 0xD3, MODEL_CF, pins
    rtnode-utils.patch           setTxPower dispatch, product whitelist, model check, LEDs
    rtnode-platformio-env.ini    append to platformio.ini

Two settings each give a board that builds and flashes perfectly and then
fails silently, so both are pinned by tests:

* **`HAS_TCXO false`.** Same crystal, same trap. The XIAO S3 block a few
  screens above in that firmware says `true`; copying the nearest neighbour
  reproduces the silent-radio failure exactly.
* **4 MB flash with QUAD PSRAM** — *not* the XIAO env's 16 MB with `opi`.
  Octal PSRAM claims GPIO 33-37, which is where this board's DIO1 (33),
  BUSY (34) and LED (37) live. Uses `partitions_4mb_ota.csv`, which exists in
  that tree precisely because the app is too large for `default.csv`.

Builds at **flash 87.1%** (1,255,961 of 1,441,792 bytes), RAM 21.2%. That is
only ~186 KB of headroom: the board's OLED is left off for this first pass,
as the XIAO target does, and turning it on has to fit in what is left.

Reaches the operator through `ui.rtnode_choice.S3_NATIVE_CARDS`. It has to be
in that pool to be choosable at all — a native-USB ESP32-S3 presents the same
Espressif identity as the Heltec V4 and the XIAO, so only the operator can
say which board is on the bench.

## Pin map, and how it was established

Two independent sources agree on every pin: the Meshtastic variant
`CDEBYTE_EoRa-S3` (whose comment names this exact chain — EoRa-S3-900TB <-
E22-900MM22S <- SX1262) and a working sketch from `Tech500/EoRa-PI-Foundation`.

    CS 7 · SCK 5 · MOSI 6 · MISO 3 · BUSY 34 · DIO1 33 · RESET 8
    LED 37 · I2C SDA 18 / SCL 17 · BOOT 0

**Confirmed on silicon**, 2026-09-08: an isolated SPI probe read the SX1262
sync word `0x1424` five times running, with BUSY going HIGH->LOW across reset.
The radio is a bare Semtech part — there is no Ebyte MCU or proprietary
protocol in the path (that is the E22-*T*22S / E220 family, which expose UART).

Ebyte's own product page disagrees on DIO1/BUSY/LED and also calls this a
433 MHz board; it was discarded as misread. Do **not** use the Meshtastic
`EBYTE_ESP32-S3` variant — that is a hand-wired E22 on a generic WROOM board
and shares none of these pins.

## The five gaps this port had to close

Each one was invisible until the board was in hand, and each produces a
board that flashes perfectly and then fails silently in a different way:

1. **`CDCOnBoot`** — the generic `esp32s3` FQBN leaves it disabled, mapping
   the firmware's `Serial` to UART0 on GPIO 43/44. The board is then mute to
   `rnodeconf` over USB. FQBN pins `FlashSize=4M,CDCOnBoot=cdc` (the part is
   an ESP32-S3FH4R2: 4 MB flash, 2 MB PSRAM, **quad** — which is what leaves
   GPIO 33/34/37 free for the radio).
2. **`MODEL_CF` missing from the `setTxPower` dispatch** in `Utilities.h` — a
   model not in that list never gets `setTxPower()` called, so TX power reads
   0 dBm forever.
3. **`PRODUCT_EORA_S3` missing from `eeprom_product_valid()`** — a hardcoded
   whitelist. Without it the firmware judges its own EEPROM invalid, never
   sets `model`, and nothing downstream works.
4. **`HAS_TCXO` — this board has a plain crystal, and saying otherwise
   silently kills the radio.** The trap is that the obvious reading of the
   symptom points the wrong way. Leaving `HAS_TCXO true` falls through
   `enableTCXO()` to an `#else` that sends all zeros — 1.6 V *and a zero
   stabilisation timeout* — and the radio reports offline forever. The
   tempting fix is to add a board branch with a sane 1.8 V TCXO setting. That
   makes the radio come online, report correct parameters, accept transmits
   and advance every counter — **while emitting and hearing nothing at all.**
   The answer is `HAS_TCXO false`: no DIO3 TCXO control, and `standby()`
   correctly uses `STDBY_RC` instead of `STDBY_XOSC`. Two sources say so —
   Meshtastic's variant ("CDEBYTE EoRa-S3 uses an XTAL, thus we do not need
   DIO3 as TCXO voltage reference", while its sister EoRa-Hub *does* declare
   one, so the omission is deliberate) and `Tech500/EoRa-PI-Foundation`
   passing RadioLib `0.0, // No TCXO (EoRa Pi uses XTAL)`. Note also that
   `calibrate()` and `calibrate_image()` run *before* `enableTCXO()` in
   `Radio.cpp`, so a late clock-source switch leaves the part calibrated for
   a configuration it is no longer in.
5. **`Display.h` keeps its own board chains**, independent of `HAS_DISPLAY` in
   `Boards.h`. The fatal one is the `Wire.begin()` chain: with no matching
   branch the I2C bus is never started on this board's pins, so the panel is
   never addressed and simply keeps whatever the Ebyte demo left in it. The
   geometry chain needs a branch too — the `#else` gets the address right but
   defines no `SCL_OLED`/`SDA_OLED` at all, so it would not even compile.

## Provisioning

    rnodeconf <port> --eeprom-wipe
    rnodeconf <port> -r --platform ESP32 --product d3 --model cf --hwrev 1
    rnodeconf <port> -T --freq 915125000 --bw 125000 --sf 9 --cr 5 --txp 17
    rnodeconf <port> --firmware-hash $(./partition_hashes build/esp32.esp32.esp32s3/RNode_Firmware_CE.ino.bin)

`rnodeconf` also needs to learn the board — see `eora-s3-rnodeconf.patch`
(product `0xD3`, model `0xCF`); stock upstream raises `KeyError` without it.

## WORKING — two-way radio contact confirmed 2026-09-08

Identity `Ebyte EoRa-S3 863 - 928 MHz (d3:cf:47)`, signature validated,
MTU 508, 915.125 MHz / BW125 / SF9 / CR5 / 17 dBm.

### Proof, and why the earlier proof was not proof

Verified against the medic's own RNode, inches away, 17 dBm, same band. Both
directions, each measured at the FAR end rather than at the sender:

* **Receive:** the medic's RNode announced ten times; `rxb` on the EoRa went
  0 -> **2379 bytes**.
* **Transmit:** the EoRa announced; the medic's RNode received-byte counter
  moved **22.28 KB -> 24.00 KB**.
* **Noise floor -105.0 dBm on all 230 samples**, read from the firmware's own
  framebuffer. The medic's RNode, same room: -106 dBm. Within 1 dB.

**Earlier the same day this board was reported working, and it was not.** A
five-minute soak showed 10 announces, 0 drops, `txb` 1670, radio online,
signature validated — and not one byte ever left the antenna. Every one of
those figures was measured at the sending end. `txb` counts what the host
handed the modem. "Radio online" means the modem answered. Neither is radio.

Before the fix, the same two tests read: **RX 0 bytes** through ten announces,
and **TX unheard** — the far-end counter did not move at all. The waterfall
sat at -90 dBm with half its samples pinned at the top of the scale and 100%
channel load, against a -106 dBm control in the same room. That 16-46 dB gap
against a quiet control was the tell.

**The rule this cost an evening to learn: a counter advancing is not radio
contact. Nothing is on air until a second radio has heard it.**

### The 0.96" OLED — verified without looking at it

The firmware can be asked what it is drawing. `CMD_DISP_READ` (`0x66`) dumps
`disp_area` then `stat_area`, two 64x64 `GFXcanvas1` buffers, 512 bytes each,
which stitch into the 128x64 panel. Those buffers are only ever drawn into
when `disp_ready` is true, and `disp_ready` is `display_init()`'s return
value — so a non-blank readback is proof the SSD1306 acknowledged on I2C and
the panel is genuinely being driven.

That is a better test than a photograph: it is exact, it is scriptable, and it
distinguishes "the panel is dark" from "the firmware is drawing nothing".
Decode the buffer MSB-first, 8 bytes per row, and render it to PNG.

Confirmed 2026-09-08: splash renders, and after the firmware hash is written
the panel reads `DEVICE CHECKS PASSED` instead of `FIRMWARE CORRUPT`.

**The waterfall only moves while a host has the radio on.** `draw_waterfall()`
is gated on `radio_online` (Display.h), so with no host attached the radio is
off, the waterfall stops being drawn, and the **last frame stays on screen**.
It reads exactly like a hung board. Measured with the framebuffer readback
above: radio on, 22-31 of the waterfall's 46 rows change every 2 s; radio off,
zero. This is upstream RNode behaviour, not something this port introduced.

The left-hand pane keeps cycling its three pages either way, which makes the
frozen waterfall beside it look even more like a fault. It is not one.

**The waterfall only moves while a host has the radio on.** `draw_waterfall()`
is gated on `radio_online`, so with no host attached the radio is off, the
waterfall stops being drawn, and the **last frame stays on screen**. Beside a
left-hand pane that keeps cycling its three pages, that reads exactly like a
hung board. It is upstream RNode behaviour, not something this port added.

Measured with the framebuffer readback: radio on, 22-31 of the waterfall's 46
rows change every 2 s; radio off, zero. Note that "it has content" is not the
same as "it is moving" — two stills an evening apart looked identical here and
sent the diagnosis down the wrong road for a while.

**Reflashing invalidates the firmware hash.** A new binary hashes differently,
so the panel reads `FIRMWARE CORRUPT` until the hash is written again —
`rnodeconf <port> --get-firmware-hash` reads what the device computed from its
own flash, and `-H <hash>` stores it back. The radio works either way; only
the display and the signature check care.

### The last two fixes, and they are not board-specific

**1. RNS gave serial 0.2 s to answer** (`rns-serial-detect.patch`). In
`RNodeInterface.configure_device()`, TCP and BLE each get a 5-second polling
loop; the serial branch was a flat `sleep(0.2)` with no polling. Opening a
serial port asserts DTR/RTS, which on an ESP32-S3's USB-JTAG **resets the
MCU** — so the board has to finish booting inside 2.0 s + 0.2 s or be declared
absent. This took connection from ~2-in-5 to 8/8. It should help any
native-USB ESP32-S3 RNode, not just this one.

**2. `preInit()` never reset the radio** (`radio.patch`). It identifies the
modem by reading sync-word registers; a power cycle resets the SX1262 too, so
that read works, but any warm reset leaves the radio in its previous state.
`reset()` also waited a flat 10 ms and never waited for BUSY to fall, while
`waitOnBusy()` gives up after 100 ms and **proceeds anyway**. Both fixed.

### Methodology warning — this cost hours

Repeatedly opening and closing the port is **not** what a node does, and it is
a hostile test for a board that resets on every open. Measured on this board:
4/4 connects before a transmit, 1/4 after — which looked like "transmitting
breaks it". It does not. Held open, it ran 5 minutes and 10 transmits with
zero drops. Test the deployment pattern, not the convenient one.

Related trap: `rnodeconf --info` reported "did not respond" on a board that
RNS connected to and transmitted through seconds later. rnodeconf has a
shorter handshake tolerance; its failure is not evidence of a dead board.

### Theories tested and DISPROVED (recorded so nobody repeats them)

* **Boot vector.** `boot_flags` is hardcoded `0x02` with a `// TODO`, so the
  vector always passes. Not it.
* **`bt_ready` / firmware signature.** Built with `VALIDATE_FIRMWARE false`,
  bypassing both; radio still 0/3. Conclusively not it.
* **USB mode.** OTG (`USBMode=default`) changed nothing AND **breaks automatic
  flashing** — the hardware USB-JTAG bridge is what lets esptool enter download
  mode unaided. Do not switch this board to OTG.
* **Power/brownout on TX.** Low-power (2 dBm) transmits behaved the same.
* **A settling period after TX.** Waiting 90 s changed nothing.

### Two self-inflicted traps

* **`--erase-all` wipes the EEPROM**, not just the app — provisioning must be
  redone, and it fixes nothing.
* **A DTR/RTS reset pulse straps the board into ROM download mode**
  (`boot:0x11 DOWNLOAD`) because DTR drives GPIO0 on the USB-JTAG. Repeated
  "the board is stuck" readings were the diagnostic causing the fault it was
  measuring. Read the port passively before believing a stuck-board diagnosis.
* Recovery without touching the board: a **1200-baud touch** re-enumerates it
  as the JTAG unit and it accepts a flash again. The port number moves.
* `uhubctl` can power-cycle a single port with passwordless sudo on the medic:
  `sudo uhubctl -l 3 -p 1 -a cycle`. **Kernel numbering is not the case
  markings** — kernel hub3-port1 is the case's port 4. Confirm by serial
  before cycling, and never cycle the medic's own radio.
