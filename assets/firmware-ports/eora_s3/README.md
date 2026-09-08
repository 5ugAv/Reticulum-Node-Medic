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

## KNOWN UNRESOLVED, 2026-09-08

The radio comes online — "configured and powered up", `online: True`, MTU 508
— but only **immediately after a true power cycle**. On a later port open it
reports `Radio state mismatch` and stays offline.

`device_init()` (Device.h) returns false unless `bt_ready` **and**
`fw_signature_validated`, and the firmware's signature hash is computed over
`dev_bt_mac + eeprom_signature`. The suspicion is a BLE-init ordering or
BT-MAC issue on this BLE-only board (`HAS_BLUETOOTH false`, `HAS_BLE true`),
not the radio. Ruled out along the way: the boot-vector path (`boot_flags` is
hardcoded `0x02`, so it always passes) and USB mode (tried both `hwcdc` and
OTG `USBMode=default`; no difference).

Not a hardware fault — the SPI probe above proves the radio is healthy.
