// RTNode-2400 — Health Beacon payload packer (v1)
//
// Packs the 14-byte, big-endian health-beacon payload carried in the app_data
// of a periodic RNS announce on the `rtnode.health` aspect. This is the wire
// contract shared with the field tool's decoder
// (reticulum-tool: monitor/health_beacon.py, struct ">BIHbBBBBBB").
//
// Kept pure (stdint only, no Arduino/RNS deps) so the exact byte layout can be
// unit-tested off-device against the tool's golden vector. The higher-level
// builder that pulls live values from collect_health() lives in HealthBeacon.h.
//
// Wire layout (big-endian):
//   [0]      format version (0x01)
//   [1..4]   uptime seconds            (uint32)
//   [5..6]   free heap KB              (uint16, low-water/min preferred)
//   [7]      WiFi RSSI dBm             (int8; 0 when WiFi down)
//   [8]      reset reason              (0 poweron,1 panic,2 brownout,3 task_wdt,
//                                       4 sw,5 other)
//   [9]      flags                     (see HB_FLAG_* below)
//   [10]     board id                  (= BOARD_MODEL; 0x3F = Heltec V4)
//   [11..13] firmware version          (major, minor, patch)
//
// Newer formats may APPEND bytes; the first 14 stay stable (decoder tolerates
// trailing bytes).

#ifndef HEALTHBEACONPACK_H
#define HEALTHBEACONPACK_H

#include <stdint.h>
#include <stdbool.h>

#define HEALTH_BEACON_FORMAT_VERSION 0x01
#define HEALTH_BEACON_LEN            14

// On-demand poll request opcode (1-byte packet to the rtnode.health dest).
// Matches reticulum-tool monitor/health_poll.OPCODE_FULL_HEALTH. Unknown
// opcodes are ignored by the firmware so the registry can grow independently.
#define HB_OPCODE_FULL_HEALTH        0x01
// Identify: replay the birth-cry LED choreography so an operator can pick this
// physical node out of a pile ("which board is Rooftop-East?"). No reply.
#define HB_OPCODE_IDENTIFY           0x02
// LED test: hold SOLID GREEN for ~15 s — verify the pixel is wired/alive and
// give the operator a photo/probe window. No reply.
#define HB_OPCODE_LED_TEST           0x03

// Flag bit positions — MUST match reticulum-tool monitor/health_beacon.py.
enum {
    HB_FLAG_WIFI_UP         = 0x01,  // bit0
    HB_FLAG_LORA_UP         = 0x02,  // bit1
    HB_FLAG_TCP_BACKBONE_UP = 0x04,  // bit2
    HB_FLAG_LOCAL_TCP_UP    = 0x08,  // bit3
    HB_FLAG_WDT_ARMED       = 0x10,  // bit4
    HB_FLAG_PSRAM           = 0x20,  // bit5
    HB_FLAG_FAULT           = 0x40,  // bit6 (confirmed persistent fault)
    HB_FLAG_AIRTIME_LOCK    = 0x80,  // bit7 (duty-cycle limiter engaged)
};

// Reset-reason enum values (match the tool's RESET_REASONS map).
enum {
    HB_RESET_POWERON  = 0,
    HB_RESET_PANIC    = 1,
    HB_RESET_BROWNOUT = 2,
    HB_RESET_TASK_WDT = 3,
    HB_RESET_SW       = 4,
    HB_RESET_OTHER    = 5,
};

// ─── v2: power + link tail ───────────────────────────────────────────────────
// v2 APPENDS a 6-byte power+link tail after the v1 prefix, so every birthed node
// (RTNode-2400 VBAT, Pi+RNode UPS) can report battery + transmission. The prefix
// is byte-identical to v1 except [0] carries 0x02; a v1 decoder still reads the
// shared prefix, and the tool's decoder reads the tail when the payload is long
// enough. MUST match reticulum-tool monitor/health_beacon.py (">HBBbb" tail).
//
//   [14..15] battery millivolts   (uint16; 0 = not reported)
//   [16]     battery percent       (uint8; 0xFF = unknown)
//   [17]     power flags           (HB_PWR_* below)
//   [18]     LoRa link SNR dB       (int8; -128 = unknown) — node's own view
//   [19]     LoRa link RSSI dBm     (int8; -128 = unknown) — node's own view
#define HEALTH_BEACON_FORMAT_VERSION_V2 0x02
#define HEALTH_BEACON_LEN_V2            20

enum {
    HB_PWR_ON_BATTERY = 0x01,  // bit0: running from battery (not external)
    HB_PWR_CHARGING   = 0x02,  // bit1: battery charging
    HB_PWR_SOLAR      = 0x04,  // bit2: solar input present
    HB_PWR_MAINS      = 0x08,  // bit3: wall/DC external input present
};

// "Not reported" sentinels — MUST match the decoder.
#define HB_BATTERY_MV_UNKNOWN   0x0000
#define HB_BATTERY_PCT_UNKNOWN  0xFF
#define HB_LORA_LINK_UNKNOWN    (-128)

// Packs the payload. Bytes are written big-endian explicitly (shift/mask) so
// the output is identical regardless of host endianness.
static inline void health_pack_beacon(
    uint8_t  out[HEALTH_BEACON_LEN],
    uint32_t uptime_s,
    uint16_t heap_kb,
    int8_t   rssi_dbm,
    uint8_t  reset_code,
    bool wifi_up, bool lora_up, bool tcp_backbone_up, bool local_tcp_up,
    bool wdt_armed, bool psram, bool fault, bool airtime_lock,
    uint8_t  board_id,
    uint8_t  fw_major, uint8_t fw_minor, uint8_t fw_patch)
{
    uint8_t flags = 0;
    if (wifi_up)         flags |= HB_FLAG_WIFI_UP;
    if (lora_up)         flags |= HB_FLAG_LORA_UP;
    if (tcp_backbone_up) flags |= HB_FLAG_TCP_BACKBONE_UP;
    if (local_tcp_up)    flags |= HB_FLAG_LOCAL_TCP_UP;
    if (wdt_armed)       flags |= HB_FLAG_WDT_ARMED;
    if (psram)           flags |= HB_FLAG_PSRAM;
    if (fault)           flags |= HB_FLAG_FAULT;
    if (airtime_lock)    flags |= HB_FLAG_AIRTIME_LOCK;

    out[0]  = HEALTH_BEACON_FORMAT_VERSION;
    out[1]  = (uint8_t)(uptime_s >> 24);
    out[2]  = (uint8_t)(uptime_s >> 16);
    out[3]  = (uint8_t)(uptime_s >> 8);
    out[4]  = (uint8_t)(uptime_s);
    out[5]  = (uint8_t)(heap_kb >> 8);
    out[6]  = (uint8_t)(heap_kb);
    out[7]  = (uint8_t)rssi_dbm;      // two's-complement byte for int8
    out[8]  = reset_code;
    out[9]  = flags;
    out[10] = board_id;
    out[11] = fw_major;
    out[12] = fw_minor;
    out[13] = fw_patch;
}

// Packs the 20-byte v2 payload: the v1 prefix (with version byte bumped to 0x02)
// plus the power+link tail [14..19]. Pass the HB_*_UNKNOWN sentinels for any
// value the board can't read; the decoder maps them back to "not reported".
static inline void health_pack_beacon_v2(
    uint8_t  out[HEALTH_BEACON_LEN_V2],
    uint32_t uptime_s,
    uint16_t heap_kb,
    int8_t   rssi_dbm,
    uint8_t  reset_code,
    bool wifi_up, bool lora_up, bool tcp_backbone_up, bool local_tcp_up,
    bool wdt_armed, bool psram, bool fault, bool airtime_lock,
    uint8_t  board_id,
    uint8_t  fw_major, uint8_t fw_minor, uint8_t fw_patch,
    uint16_t battery_mv, uint8_t battery_pct, uint8_t power_flags,
    int8_t   lora_snr_db, int8_t lora_rssi_dbm)
{
    // v1 prefix — identical layout (writes out[0..13]; the 14-sized param decays
    // to a pointer so passing the larger v2 buffer is well-defined).
    health_pack_beacon(out, uptime_s, heap_kb, rssi_dbm, reset_code,
        wifi_up, lora_up, tcp_backbone_up, local_tcp_up,
        wdt_armed, psram, fault, airtime_lock,
        board_id, fw_major, fw_minor, fw_patch);
    out[0]  = HEALTH_BEACON_FORMAT_VERSION_V2;   // bump version in the prefix
    // power+link tail (big-endian, explicit shift/mask).
    out[14] = (uint8_t)(battery_mv >> 8);
    out[15] = (uint8_t)(battery_mv);
    out[16] = battery_pct;
    out[17] = power_flags;
    out[18] = (uint8_t)lora_snr_db;    // two's-complement byte for int8
    out[19] = (uint8_t)lora_rssi_dbm;
}

#endif // HEALTHBEACONPACK_H
