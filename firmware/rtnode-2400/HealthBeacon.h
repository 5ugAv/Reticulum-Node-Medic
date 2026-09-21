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
#if !defined(ESP32)
    return HB_RESET_OTHER;   // nRF52: reset-reason not wired yet — honest
#else
    switch (esp_reset_reason()) {
        case ESP_RST_POWERON:  return HB_RESET_POWERON;
        case ESP_RST_PANIC:    return HB_RESET_PANIC;
        case ESP_RST_BROWNOUT: return HB_RESET_BROWNOUT;
        case ESP_RST_TASK_WDT: return HB_RESET_TASK_WDT;
        case ESP_RST_SW:       return HB_RESET_SW;
        default:               return HB_RESET_OTHER;
    }
#endif
}

// Gather live health into the 20-byte v2 wire payload (v1 prefix + power/link
// tail). `fault` is supplied by the caller (the 3-attempt fault debounce lands
// in a later increment; false today). Battery is board-gated in collect_health:
// until a verified VBAT pin is enabled it packs the "not reported" sentinels, so
// the payload is always a valid v2 beacon the tool decodes.
#if HAS_GPS
// v3: this board can know where it stands (T114 + L76K). The gps object
// lives in the .ino; TinyGPSPlus.h's include guard makes this safe.
#include <TinyGPSPlus.h>
extern TinyGPSPlus gps;
#define HEALTH_BEACON_LEN_LOCAL HEALTH_BEACON_LEN_V3
#else
#define HEALTH_BEACON_LEN_LOCAL HEALTH_BEACON_LEN_V2
#endif

inline void health_build_beacon(uint8_t out[HEALTH_BEACON_LEN_LOCAL], bool fault = false) {
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
        | (h.on_mains   ? HB_PWR_MAINS      : 0)
        // the live Bluetooth verdict (operator, 2026-08-21: the board's
        // screen said BT active while VITALS said unknown — the beacon
        // simply had no word for it). KNOWN set on every beacon from this
        // firmware; UP mirrors the same source the screen row uses.
        | HB_PWR_BT_KNOWN
        | ((bt_state != BT_STATE_OFF) ? HB_PWR_BT_UP : 0);

#if HAS_GPS
    // v3 position tail: the node's OWN live claim about where it stands.
    // Sentinel when there's no FRESH fix (valid + < 10 s old — the
    // telemetry-fresh-vs-actual-fix trap) so a stale place is never
    // announced. fuzzed=false: kin nodes tell their medic the truth; the
    // wild-node fuzz policy rides the same bit when it lands.
    int32_t lat_u = HB_POSITION_UNKNOWN, lng_u = HB_POSITION_UNKNOWN;
    bool gps_fresh = gps.location.isValid() && gps.location.age() < 10000;
    if (gps_fresh) {
        lat_u = (int32_t)lround(gps.location.lat() * 1000000.0);
        lng_u = (int32_t)lround(gps.location.lng() * 1000000.0);
    }
    health_pack_beacon_v3(out,
        uptime_s, heap_kb, rssi, health_reset_reason_code(),
        h.wifi_connected, h.lora_online, h.tcp_backbone_connected,
        h.local_tcp_server_up, h.wdt_armed, h.psram, fault, airtime_lock,
        (uint8_t)BOARD_MODEL,
        RTNODE_FW_MAJOR, RTNODE_FW_MINOR, RTNODE_FW_PATCH,
        h.battery_mv, h.battery_pct, power_flags,
        h.lora_snr_db, h.lora_rssi_dbm,
        lat_u, lng_u, (uint8_t)gps.satellites.value(), false);
#else
    health_pack_beacon_v2(out,
        uptime_s, heap_kb, rssi, health_reset_reason_code(),
        h.wifi_connected, h.lora_online, h.tcp_backbone_connected,
        h.local_tcp_server_up, h.wdt_armed, h.psram, fault, airtime_lock,
        (uint8_t)BOARD_MODEL,
        RTNODE_FW_MAJOR, RTNODE_FW_MINOR, RTNODE_FW_PATCH,
        h.battery_mv, h.battery_pct, power_flags,
        h.lora_snr_db, h.lora_rssi_dbm);
#endif
}

// Emit one beacon announce immediately (also used by the on-demand poll reply).
inline void health_beacon_send() {
    if (!health_destination) return;
    uint8_t payload[HEALTH_BEACON_LEN_LOCAL];
    health_build_beacon(payload, health_fault);
    RNS::Bytes app_data;
    app_data.append(payload, HEALTH_BEACON_LEN_LOCAL);
    health_destination.announce(app_data);
    // Verification log: the exact bytes on the wire + the destination hash the
    // tool keys on. Decode against monitor/health_beacon.py.
    Serial.printf("[HealthBeacon] announce dst=%s data=%s\r\n",
                  health_destination.hash().toHex().c_str(),
                  app_data.toHex().c_str());
}

// WHO IS ALLOWED TO COMMAND THIS NODE? Nobody is authenticated here, and
// nothing in Reticulum makes them: this is a SINGLE destination, so anyone who
// has heard our announce holds the public key needed to encrypt a packet to
// it. Every branch below therefore runs for any stranger within radio range.
//
// What that bought an attacker before this guard (audit, 2026-09-09):
//   * 0x01 — make the node TRANSMIT, as often as they like. Airtime is the
//     scarcest shared resource on this mesh and it is not even ours to spend;
//   * 0x02 — ~4 s of blocking LED choreography;
//   * 0x03 — FIFTEEN SECONDS of delay() inside the packet callback. Repeat it
//     and a relay is simply off the air, which is a denial of service against
//     every node behind it — and this project's own rule is that availability
//     outranks confidentiality.
//
// Two guards, neither of which needs a shared secret we do not have:
//   1. a minimum gap between anything this handler will act on, so commanding
//      a node costs the attacker far more than it costs the node;
//   2. the two BLOCKING bench operations are compiled OUT by default. The
//      tool has never sent them (reticulum-tool monitor/health_poll only ever
//      builds 0x01); a field node has no use for them; and a build for the
//      bench can turn them on deliberately with -DHB_REMOTE_BENCH_OPS=1.
#ifndef HB_REMOTE_BENCH_OPS
#define HB_REMOTE_BENCH_OPS 0
#endif
#ifndef HB_REQUEST_MIN_GAP_MS
#define HB_REQUEST_MIN_GAP_MS 30000UL
#endif

static uint32_t health_last_request_ms = 0;

inline bool health_request_allowed() {
    uint32_t now = millis();
    // millis() wraps after ~49 days; the subtraction is unsigned, so the
    // difference stays correct across the wrap. Zero means "never yet".
    if (health_last_request_ms != 0 &&
        (now - health_last_request_ms) < HB_REQUEST_MIN_GAP_MS) {
        return false;
    }
    health_last_request_ms = now ? now : 1;
    return true;
}

inline void health_request_handler(const RNS::Bytes& data, const RNS::Packet& packet) {
    (void)packet;
    if (data.size() < 1) return;    // empty: no-op (deliberately not a fault)
    if (data[0] != HB_OPCODE_FULL_HEALTH
        && data[0] != HB_OPCODE_IDENTIFY
        && data[0] != HB_OPCODE_LED_TEST) {
        return;                     // unknown opcode: the registry can grow
    }
    if (!health_request_allowed()) {
        Serial.printf("[HealthBeacon] request 0x%02X ignored - within %lu ms "
                      "of the last one\r\n",
                      (unsigned)data[0], (unsigned long)HB_REQUEST_MIN_GAP_MS);
        return;
    }
    if (data[0] == HB_OPCODE_FULL_HEALTH) {
        Serial.println("[HealthBeacon] on-demand poll request (0x01) -> announcing now");
        health_beacon_send();
        // Visible acknowledgement at the node: two green pulses (operator
        // request — "pulse the green sequence twice on health check"). Reply
        // is already on the air; ~0.5 s of LED time after it is harmless.
        health_ack_blink();
    }
#if HB_REMOTE_BENCH_OPS
    else if (data[0] == HB_OPCODE_IDENTIFY) {
        // Identify: replay the birth cry so the operator can spot this exact
        // board on the bench. Blocking ~4 s, which is why it is off by default.
        Serial.println("[HealthBeacon] identify request (0x02) -> birth cry");
        birth_cry();
    }
    else if (data[0] == HB_OPCODE_LED_TEST) {
        // LED test: solid green long enough to photograph / probe the pixel
        // wiring. Blocking 15 s — bench builds only, never a field node.
        Serial.println("[HealthBeacon] LED test (0x03) -> solid green 15s");
        #if defined(HAS_NP) && HAS_NP == true
        npset(0, 0xFF, 0);
        delay(15000);
        npset(0, 0, 0);
        #endif
    }
#else
    else {
        Serial.printf("[HealthBeacon] request 0x%02X refused - blocking bench "
                      "ops are not compiled into this build\r\n",
                      (unsigned)data[0]);
    }
#endif
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

#if defined(ESP32)
    uint32_t heap = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
#else
    uint32_t heap = dbgHeapFree();   // Adafruit nRF52 core's free-heap read
#endif
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
