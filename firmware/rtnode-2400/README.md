# RTNode-2400 firmware — health-beacon source

These are the **health-beacon** source files for the RTNode-2400 (ESP32) fork —
the firmware side of the health contract the Node Medic decodes. They are
version-controlled here so the wire format lives beside its Python counterpart
(`monitor/health_beacon.py`) and the two can never drift silently.

| File | Role |
|------|------|
| `HealthBeaconPack.h` | Pure `stdint` byte-exact wire packer (v1 + v2). No Arduino/RNS deps, so it compiles and unit-tests off-device against the tool's golden vector. |
| `HealthStatus.h` | `collect_health()` — reads live board state (uptime, heap, WiFi, LoRa, and the v2 battery/power fields). Also serves `GET /status`. |
| `HealthBeacon.h` | The RNS announcer: builds the payload, announces on `rtnode.health`, and answers the medic's on-demand `0x01` poll (the "commandable lighthouse"). |

| `BirthCry.h` | LED choreography: the first-boot-after-flash **birth cry** (faint bubbling rainbow → smooth swell → bright white pulse → two white blinks; NVS-gated to the build stamp so it plays once per new flash) + the green double-pulse health-check acknowledgement. No-ops on boards without a NeoPixel. |

The **full** RTNode-2400 firmware fork (the buildable PlatformIO/arduino-cli
tree) is large and upstream-derived; it lives on the medic at `~/RTNode-2400/`.
Only these sources are vendored here. To flash a node, these files are copied
into that tree and built there, **plus two one-line hooks in
`RNode_Firmware.ino`** (kept in the medic's tree, re-apply if rebuilding from a
pristine fork):

1. `#include "BirthCry.h"` immediately after `#include "Utilities.h"` (needs
   `npset`; must precede `HealthBeacon.h`, which calls `health_ack_blink`).
2. `birth_cry_maybe();` as the last line of `setup()`.

## Wire contract (shared, version-pinned)

The health beacon is the `app_data` of a periodic RNS announce on aspect
`rtnode.health`. **v2** appends a 6-byte power+link tail after the v1 prefix:

```
[0]      format version (0x01 v1 / 0x02 v2)
[1..4]   uptime seconds        uint32
[5..6]   free heap KB          uint16 (low-water)
[7]      WiFi RSSI dBm         int8 (0 = down)
[8]      reset reason          enum
[9]      flags                 wifi/lora/backbone/localtcp/wdt/psram/fault/airtime
[10]     board id              0x3F = Heltec V4
[11..13] firmware version      major, minor, patch
-- v2 tail (only when [0] >= 0x02) --
[14..15] battery millivolts    uint16 (0 = not reported)
[16]     battery percent       uint8  (0xFF = unknown)
[17]     power flags           on_battery / charging / solar / mains
[18]     LoRa link SNR dB       int8 (-128 = unknown)
[19]     LoRa link RSSI dBm     int8 (-128 = unknown)
```

The decoder tolerates trailing bytes, so a future v3 can append again without a
lockstep release. Golden vectors are asserted byte-for-byte on both sides:
`tests/test_firmware_beacon_contract.py` compiles `HealthBeaconPack.h` with g++
and checks its output equals `monitor/health_beacon.encode(...)`.

## Battery: the firmware's own PMU path — never a guessed pin

`collect_health` reads the **firmware's existing battery globals**
(`battery_installed` / `battery_voltage` / `battery_percent` /
`battery_state`), which `Power.h`'s `measure_battery()` maintains from `loop()`
with vendor-verified per-board pins (Heltec V4 & V3: `pin_vbat=1`,
`pin_ctrl=37`) and charge-state detection. A board with no battery attached
honestly packs the "not reported" sentinels. `battery_state`
CHARGING/CHARGED both map to the beacon's `charging` flag (on external power —
a low battery then never raises a battery alert).

Fallback only: boards whose `Power.h` has no PMU section can use the explicit
board-gated ADC read — inert until `RTNODE_VBAT_ADC_PIN` /
`RTNODE_VBAT_CTRL_PIN` / `RTNODE_VBAT_DIVIDER` are **defined after bench
verification**. Never guess the pin.

## Bench checklist to verify battery on an RTNode-2400

1. Wire the battery to the board's **Bat JST** (the managed input) — that's the
   input `measure_battery()` senses.
2. Build + flash, watch the `[HealthBeacon] announce … data=02…` line — the
   payload should carry a real voltage (bytes [14..15] non-zero).
3. Verify on the medic: VITALS ▸ tap node shows e.g. "Battery: 84%  3.97 V
   Power: battery (charging)" — and cross-check the voltage with a multimeter
   if it looks off.
4. For a solar node add `RTNODE_POWER_SOLAR` to the build flags (stamps the
   power-flags bit so the medic knows the recharge model).
