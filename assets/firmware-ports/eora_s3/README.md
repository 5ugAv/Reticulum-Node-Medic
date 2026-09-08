# Ebyte EoRa-S3-900TB — RNode firmware port

Four patches against **RNode_Firmware_CE** (baseline `a42f8d3`). Apply in a
clean CE checkout, then `make firmware-eora_s3`.

    patch -p1 < boards.patch      # BOARD_EORA_S3 0x47, PRODUCT 0xD3, MODEL_CF 0xCF, pin map, LEDs
    patch -p1 < utils.patch       # setTxPower dispatch, eeprom_product_valid, model check, LED fns
    patch -p1 < radio.patch       # TCXO at 1.8V (see below)
    patch -p1 < make.patch        # the firmware-eora_s3 target

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

## The four gaps this port had to close

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
4. **`BOARD_EORA_S3` missing from `enableTCXO()`** in `Radio.cpp` — falls
   through to an `#else` that sends all zeros: 1.6 V *and a zero stabilisation
   timeout*. The E22 ties DIO3 to a 1.8 V TCXO reference.

## Provisioning

    rnodeconf <port> --eeprom-wipe
    rnodeconf <port> -r --platform ESP32 --product d3 --model cf --hwrev 1
    rnodeconf <port> -T --freq 915125000 --bw 125000 --sf 9 --cr 5 --txp 17
    rnodeconf <port> --firmware-hash $(./partition_hashes build/esp32.esp32.esp32s3/RNode_Firmware_CE.ino.bin)

`rnodeconf` also needs to learn the board — see `eora-s3-rnodeconf.patch`
(product `0xD3`, model `0xCF`); stock upstream raises `KeyError` without it.

## KNOWN UNRESOLVED, 2026-09-08 — connection is a coin-flip

Fully working when it connects: `online: True`, "configured and powered up",
MTU 508, 17 dBm at 915.125/BW125/SF9/CR5, identity `d3:cf:47`, signature
validated. **But only about 2 connection attempts in 5 succeed.**

### What it is

A **race on port open**, not a radio fault. Opening the serial port asserts
DTR/RTS, which on this chip's USB-JTAG resets the MCU. Sometimes the board
finishes booting before RNS's detect times out; sometimes it does not.

### The one real fix found so far (in radio.patch)

`sx126x::preInit()` identifies the modem by reading its sync-word registers
and **never reset the part first**. A power cycle resets the SX1262 along with
the MCU, so the read succeeds; any warm reset leaves the radio in whatever
state the last session left it in. Adding `reset()` to `preInit()` took the
success rate from 0/3 to 2/5. This is probably an upstream issue affecting
every SX126x board, not just this one — worth reporting.

### Theories tested and DISPROVED (recorded so nobody repeats them)

* **Boot vector.** `boot_flags` is hardcoded `0x02` with a `// TODO`, so the
  vector is always START_FROM_BOOTLOADER and always passes. Not it.
* **`bt_ready` / firmware signature.** Built with `VALIDATE_FIRMWARE false`,
  which makes `device_init()` return true immediately and bypasses both. The
  radio still would not come online: **0/3**. Conclusively not it.
* **USB mode.** Tried OTG (`USBMode=default`) as well as hwcdc. No difference
  to the race — and OTG **breaks automatic flashing**, because it is the
  hardware USB-JTAG bridge that lets esptool enter download mode by itself.
  Do not switch this board to OTG.

### Two traps that cost real time here

* **`--erase-all` wipes the EEPROM**, not just the app. Provisioning has to be
  redone afterwards. It also does not fix anything: a full erase + reflash
  still booted to download mode.
* **A DTR/RTS reset pulse strapped the board into ROM download mode**
  (`boot:0x11 DOWNLOAD`) — GPIO0 is driven by DTR on the USB-JTAG. Repeated
  "the board is stuck" readings were the diagnostic tool causing the fault it
  was measuring. Read the port passively (DTR/RTS released) before believing
  a stuck-board diagnosis.
* Recovery from that state without touching the board: a **1200-baud touch**
  makes it re-enumerate as the JTAG unit and accept a flash again. The port
  number moves when it does.

### Next thing to try

Lengthen RNS's detect timeout / settle delay for this board, or hold the port
open so the DTR transition happens only once. The hardware is not in question:
an isolated SPI probe reads sync word `0x1424` reliably every time.
