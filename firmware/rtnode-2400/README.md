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

The **full** RTNode-2400 firmware fork (the buildable PlatformIO/arduino-cli
tree) is large and upstream-derived; it lives on the medic at `~/RTNode-2400/`.
Only the health-beacon source is vendored here. To flash a node, these files are
copied into that tree and built there.

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

## VBAT is board-gated — never guessed

Battery sensing is OFF until `RTNODE_VBAT_ADC_PIN` (and divider) are **defined
per board after bench verification**. Until then the beacon packs the
"not reported" sentinels — honest, never a guessed pin. See the header comment in
`HealthStatus.h` for the Heltec reference pins to confirm before enabling.

## Bench checklist to activate battery on an RTNode-2400

1. Confirm the board's VBAT ADC pin + divider ratio (do **not** assume V4 == V3).
2. Define `RTNODE_VBAT_ADC_PIN` / `RTNODE_VBAT_CTRL_PIN` / `RTNODE_VBAT_DIVIDER`
   (and `RTNODE_POWER_SOLAR` for a solar node) in the build config.
3. Build + flash (e.g. FAITH), watch the `[HealthBeacon] announce … data=02…`
   line — the payload should now be 20 bytes and carry a real voltage.
4. Verify on the medic: VITALS ▸ tap node shows the battery reading.
