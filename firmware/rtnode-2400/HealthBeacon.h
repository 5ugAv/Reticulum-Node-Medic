// RTNode-2400 — Health Beacon (Phase 2)
//
// Periodically announces node health over the LoRa mesh to "home base" (the
// field diagnostic tool), and — later — answers on-demand poll requests. This
// is the push counterpart to the pull-based /status HTTP endpoint, and works
// with no WiFi (it rides the Reticulum announce over LoRa).
//
// Reuses collect_health() (HealthStatus.h) and health_pack_beacon()
// (HealthBeaconPack.h, the byte-exact wire codec). Announce mechanics are
// modeled on the proven Advertise.h interface-discovery announcer.
//
// Wire contract is shared + version-pinned with reticulum-tool
// (monitor/health_beacon.py): a 14-byte payload in the app_data of an RNS
// announce on aspect "rtnode.health", under the node's persistent identity.

#ifndef HEALTHBEACON_H
#define HEALTHBEACON_H

#include <Destination.h>
#include <Transport.h>
#include <Packet.h>

#include "HealthBeaconPack.h"
#include "HealthStatus.h"

// airtime_lock is a global bool defined in Config.h; extern here so this header
// does not depend on include ordering.
extern bool airtime_lock;

// Announce cadence: 2h in the field. The initial delay lets WiFi/LoRa settle
// and lets bench testing see the first announce without waiting hours.
#ifndef HEALTH_BEACON_INTERVAL_MS
#define HEALTH_BEACON_INTERVAL_MS       (2UL * 60UL * 60UL * 1000UL)  // 2 hours
#endif
#ifndef HEALTH_BEACON_INITIAL_DELAY_MS
#define HEALTH_BEACON_INITIAL_DELAY_MS  (30UL * 1000UL)               // 30 s
#endif

// Fault detection (payload bit b6). A fault is only CONFIRMED after internal-SRAM
// heap pressure persists across HEALTH_FAULT_STRIKES consecutive checks (the
// "3 self-heal attempts" — heap often frees itself as connections close), so a
// transient dip never cries wolf / dispatches a needless repair. On the
// false->true edge we fire an immediate beacon; the routine 2h beacon carries the
// bit in between. (A single clean-recovery crash is NOT escalated here — it is
// surfaced via the reset-reason field; repeated crashes are a separate future
// escalation via the RTC bootloop counter.)
#ifndef HEALTH_FAULT_HEAP_KB
#define HEALTH_FAULT_HEAP_KB           40         // internal-SRAM early-warning floor (KB)
#endif
#ifndef HEALTH_FAULT_STRIKES
#define HEALTH_FAULT_STRIKES           3
#endif
#ifndef HEALTH_FAULT_CHECK_INTERVAL_MS
#define HEALTH_FAULT_CHECK_INTERVAL_MS (30UL * 1000UL)               // 30 s between checks
#endif

static RNS::Destination health_destination     = {RNS::Type::NONE};
static bool             health_beacon_started   = false;
static uint32_t         health_beacon_next_run  = 0;
static uint8_t          health_fault_strikes    = 0;
static bool             health_fault            = false;
static uint32_t         health_fault_next_check = 0;

// esp_reset_reason() -> wire enum (HB_RESET_*, matches the tool's map).
inline uint8_t health_reset_reason_code() {
    switch (esp_reset_reason()) {
        case ESP_RST_POWERON:  return HB_RESET_POWERON;
        case ESP_RST_PANIC:    return HB_RESET_PANIC;
        case ESP_RST_BROWNOUT: return HB_RESET_BROWNOUT;
        case ESP_RST_TASK_WDT: return HB_RESET_TASK_WDT;
        case ESP_RST_SW:       return HB_RESET_SW;
        default:               return HB_RESET_OTHER;
    }
}

// Gather live health into the 20-byte v2 wire payload (v1 prefix + power/link
// tail). `fault` is supplied by the caller (the 3-attempt fault debounce lands
// in a later increment; false today). Battery is board-gated in collect_health:
// until a verified VBAT pin is enabled it packs the "not reported" sentinels, so
// the payload is always a valid v2 beacon the tool decodes.
inline void health_build_beacon(uint8_t out[HEALTH_BEACON_LEN_V2], bool fault = false) {
    HealthSnapshot h;
    collect_health(h);

    uint32_t uptime_s  = h.uptime_ms / 1000;
    uint32_t heap_kb32 = h.heap_internal_min / 1024;   // low-water, per contract
    uint16_t heap_kb   = (heap_kb32 > 0xFFFF) ? 0xFFFF : (uint16_t)heap_kb32;
    int32_t  r         = h.wifi_rssi;                  // 0 when WiFi down
    int8_t   rssi      = (r < -128) ? -128 : (r > 127) ? 127 : (int8_t)r;

    uint8_t power_flags =
          (h.on_battery ? HB_PWR_ON_BATTERY : 0)
        | (h.charging   ? HB_PWR_CHARGING   : 0)
        | (h.on_solar   ? HB_PWR_SOLAR      : 0)
        | (h.on_mains   ? HB_PWR_MAINS      : 0);

    health_pack_beacon_v2(out,
        uptime_s, heap_kb, rssi, health_reset_reason_code(),
        h.wifi_connected, h.lora_online, h.tcp_backbone_connected,
        h.local_tcp_server_up, h.wdt_armed, h.psram, fault, airtime_lock,
        (uint8_t)BOARD_MODEL,
        RTNODE_FW_MAJOR, RTNODE_FW_MINOR, RTNODE_FW_PATCH,
        h.battery_mv, h.battery_pct, power_flags,
        h.lora_snr_db, h.lora_rssi_dbm);
}

// Emit one beacon announce immediately (also used by the on-demand poll reply).
inline void health_beacon_send() {
    if (!health_destination) return;
    uint8_t payload[HEALTH_BEACON_LEN_V2];
    health_build_beacon(payload, health_fault);
    RNS::Bytes app_data;
    app_data.append(payload, HEALTH_BEACON_LEN_V2);
    health_destination.announce(app_data);
    // Verification log: the exact bytes on the wire + the destination hash the
    // tool keys on. Decode against monitor/health_beacon.py.
    Serial.printf("[HealthBeacon] announce dst=%s data=%s\r\n",
                  health_destination.hash().toHex().c_str(),
                  app_data.toHex().c_str());
}

// On-demand poll receiver. The tool sends a bare 1-byte packet to this same
// rtnode.health destination; opcode 0x01 = "send full health now". The reply is
// just a normal beacon announce — no return address / nonce needed (the tool
// correlates by our destination hash + freshness). Unknown opcodes are ignored,
// so the request registry can grow without a firmware lockstep release.
inline void health_request_handler(const RNS::Bytes& data, const RNS::Packet& packet) {
    (void)packet;
    if (data.size() >= 1 && data[0] == HB_OPCODE_FULL_HEALTH) {
        Serial.println("[HealthBeacon] on-demand poll request (0x01) -> announcing now");
        health_beacon_send();
        // Visible acknowledgement at the node: two green pulses (operator
        // request — "pulse the green sequence twice on health check"). Reply
        // is already on the air; ~0.5 s of LED time after it is harmless.
        health_ack_blink();
    }
    else if (data.size() >= 1 && data[0] == HB_OPCODE_IDENTIFY) {
        // Identify: replay the birth cry so the operator can spot this exact
        // board on the bench / in the field. Blocking ~4 s — acceptable, it is
        // an operator-invoked rarity.
        Serial.println("[HealthBeacon] identify request (0x02) -> birth cry");
        birth_cry();
    }
    else if (data.size() >= 1 && data[0] == HB_OPCODE_LED_TEST) {
        // LED test: solid green long enough to photograph / probe the pixel
        // wiring. Blocking 15 s — operator-invoked bench tool only.
        Serial.println("[HealthBeacon] LED test (0x03) -> solid green 15s");
        #if defined(HAS_NP) && HAS_NP == true
        npset(0, 0xFF, 0);
        delay(15000);
        npset(0, 0, 0);
        #endif
    }
    // Unknown/empty opcode: no-op (deliberately not a fault).
}

// Initialise once RNS is running (Transport::identity() available). Mirrors
// advertise_init(); idempotent.
inline void health_beacon_init() {
    if (health_beacon_started) return;
    if (!RNS::Transport::identity()) return;

    health_destination = RNS::Destination(
        RNS::Transport::identity(),
        RNS::Type::Destination::IN,
        RNS::Type::Destination::SINGLE,
        "rtnode",
        "health"
    );
    // Receive on-demand poll requests on this same destination (per contract).
    health_destination.accepts_links(false);
    health_destination.set_packet_callback(health_request_handler);

    health_beacon_started   = true;
    health_beacon_next_run  = millis() + HEALTH_BEACON_INITIAL_DELAY_MS;
    health_fault_next_check = millis() + HEALTH_FAULT_CHECK_INTERVAL_MS;
    Serial.printf("[HealthBeacon] init dst=%s, first announce in ~%lus\r\n",
                  health_destination.hash().toHex().c_str(),
                  (unsigned long)(HEALTH_BEACON_INITIAL_DELAY_MS / 1000));
}

// Heap-pressure fault check on an interval. Confirms a fault only after
// HEALTH_FAULT_STRIKES consecutive failures (giving the node time to self-heal
// between checks), escalates immediately on the false->true edge, and clears on
// recovery.
inline void health_fault_check() {
    if ((int32_t)(millis() - health_fault_next_check) < 0) return;
    health_fault_next_check = millis() + HEALTH_FAULT_CHECK_INTERVAL_MS;

    uint32_t heap = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    bool pressure = (heap < (uint32_t)HEALTH_FAULT_HEAP_KB * 1024UL);
    if (pressure) {
        if (health_fault_strikes < 0xFF) health_fault_strikes++;
    } else {
        health_fault_strikes = 0;
    }

    bool confirmed = (health_fault_strikes >= HEALTH_FAULT_STRIKES);
    if (confirmed && !health_fault) {
        health_fault = true;
        Serial.printf("[HealthBeacon] FAULT confirmed after %u strikes (heap=%u) -> immediate beacon\r\n",
                      (unsigned)health_fault_strikes, (unsigned)heap);
        health_beacon_send();
    } else if (!confirmed && health_fault) {
        health_fault = false;
        Serial.println("[HealthBeacon] fault cleared (heap recovered)");
    }
}

// Periodic loop hook. Handles millis() wrap the same way advertise_loop() does.
inline void health_beacon_loop() {
    if (!health_beacon_started) return;
    health_fault_check();
    if ((int32_t)(millis() - health_beacon_next_run) < 0) return;
    health_beacon_send();
    health_beacon_next_run = millis() + HEALTH_BEACON_INTERVAL_MS;
}

#endif // HEALTHBEACON_H
