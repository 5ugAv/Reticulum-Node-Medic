// RTNode-2400 — Health Status
//
// Exposes board health data for the field diagnostic/repair tool
// ("an OBD scanner for Reticulum nodes"). Two consumers share this layer:
//
//   Phase 1 (this file): a JSON `GET /status` endpoint served over the LAN
//           while the node is in WiFi station mode.
//   Phase 2 (future):    an LXMF health beacon pushed over the Reticulum mesh.
//           It reuses collect_health() so both paths report identical data.
//
// Design notes:
//   * No ArduinoJson dependency — the payload is small and fixed, so a
//     hand-rolled String keeps the firmware footprint down.
//   * Data is read from existing globals (firewall_state, radio_online,
//     WiFi.*, heap_caps_*). The only firmware-local dependency is
//     health_local_server_up(), defined in RNode_Firmware.ino where the
//     TcpInterface pointers are visible.
//   * The status server binds port 80. In normal (station-mode) operation
//     nothing else listens there — the Console and config-portal web servers
//     only begin() in soft-AP mode, which is mutually exclusive with this.

#ifndef HEALTHSTATUS_H
#define HEALTHSTATUS_H

// PORTED TO nRF52 (2026-08-20) so the T-Echo RTNode can health-beacon: the
// whole medic ecology (VITALS, kin folding, outage watch) keys off this
// announce, and the non-firewall build previously had NOTHING that ever
// called announce() -- a silent transport the mesh could not see. Everything
// ESP32- or firewall-only below is guarded, absent values reported honestly
// (0 / false / "unknown"), never invented.
#if defined(ESP32)
  #include <WiFi.h>
  #include <WebServer.h>
  #include <esp_system.h>
  #include <esp_heap_caps.h>
#endif

#ifdef FIREWALL_MODE
  #include "FirewallMode.h"   // FirewallState / firewall_state
#endif
#include "HealthBeaconPack.h"  // HB_* flags + v2 power/link sentinels (shared codec)

// Fork version string. NOTE: this is the single in-firmware source of truth
// for the RTNode fork version (upstream RNode's MAJ_VERS/MIN_VERS is a
// separate protocol version). Bump it in lockstep with release tags.
#ifndef RTNODE_FORK_VERSION
#define RTNODE_FORK_VERSION "0.7.0"   // 0.7.0: v2 health beacon (battery + link tail)
#endif
// Numeric components of RTNODE_FORK_VERSION for the binary health beacon.
// Keep these in sync with the string above on every release.
#define RTNODE_FW_MAJOR 0
#define RTNODE_FW_MINOR 7
#define RTNODE_FW_PATCH 0

#define RTNODE_HEALTH_PORT 80

// radio_online lives in Config.h; extern-declared here so this header does
// not depend on include ordering.
extern bool radio_online;

// Battery globals maintained by the firmware's PMU path (Config.h declares,
// Power.h's measure_battery() updates them from loop()); extern-declared for
// the same include-ordering reason. battery_state: 0x00 unknown,
// 0x01 discharging, 0x02 charging, 0x03 charged (Config.h BATTERY_STATE_*).
extern bool    battery_installed;
extern float   battery_voltage;
extern float   battery_percent;
extern uint8_t battery_state;

// Runtime status of the local (LAN) TCP server. Defined in the main sketch,
// where local_tcp_interface_ptr and the TcpInterface type are in scope.
extern bool health_local_server_up();

// ─── Snapshot ───────────────────────────────────────────────────────────────
// Plain data, populated by collect_health(). Kept serialization-agnostic so
// the Phase 2 LXMF beacon can encode it however it needs.
struct HealthSnapshot {
    const char* fork;
    const char* fw_version;
    uint8_t     rnode_maj;
    uint8_t     rnode_min;

    uint16_t    board_model;
    const char* board_name;
    bool        psram;
    uint32_t    psram_size;

    uint32_t    uptime_ms;
    const char* reset_reason;

    uint32_t    heap_internal_free;   // internal SRAM — what WiFi RX buffers need
    uint32_t    heap_internal_min;    // low-water mark since boot
    uint32_t    heap_total_free;      // internal + PSRAM

    bool        wdt_armed;
    uint16_t    wdt_timeout_s;

    bool        wifi_connected;
    int32_t     wifi_rssi;
#if defined(ESP32)
    IPAddress   wifi_ip;
#endif

    bool        lora_online;
    bool        tcp_backbone_connected;
    bool        local_tcp_server_up;
    bool        local_tcp_client_connected;

    // Power (v2 beacon). battery_mv==0 / battery_pct==0xFF => not reported (the
    // VBAT read is board-gated; see health_read_battery_mv()).
    uint16_t    battery_mv;
    uint8_t     battery_pct;
    bool        on_battery;
    bool        charging;
    bool        on_solar;
    bool        on_mains;
    // The node's own view of its LoRa link (last received packet). -128 = unknown.
    int8_t      lora_snr_db;
    int8_t      lora_rssi_dbm;

    const char* node_name;
};

inline const char* health_board_name() {
#if BOARD_MODEL == BOARD_HELTEC32_V4
    return "heltec_v4";
#elif BOARD_MODEL == BOARD_HELTEC32_V3
    return "heltec_v3";
#elif BOARD_MODEL == BOARD_TECHO
    // The detector/catalogue key, deliberately -- board names cross FOUR
    // vocabularies in the tool and the key is the one that always resolves.
    return "techo";
#else
    return "unknown";
#endif
}

inline const char* health_reset_reason_str() {
#if !defined(ESP32)
    return "unknown";       // nRF52: not wired yet -- honest, never guessed
#else
    switch (esp_reset_reason()) {
        case ESP_RST_POWERON:   return "poweron";
        case ESP_RST_EXT:       return "external";
        case ESP_RST_SW:        return "software";
        case ESP_RST_PANIC:     return "panic";        // crash / abort()
        case ESP_RST_INT_WDT:   return "int_wdt";      // interrupt watchdog
        case ESP_RST_TASK_WDT:  return "task_wdt";     // task watchdog timeout
        case ESP_RST_WDT:       return "other_wdt";
        case ESP_RST_DEEPSLEEP: return "deepsleep";
        case ESP_RST_BROWNOUT:  return "brownout";     // undervoltage
        case ESP_RST_SDIO:      return "sdio";
        default:                return "unknown";
    }
#endif
}

// ─── Battery (v2 beacon) ─────────────────────────────────────────────────────
// VBAT sensing is board-specific and the divider + ADC pin MUST be verified per
// board before enabling — a wrong pin reads garbage and a wrong divider reports
// a false voltage that could trip a low-battery alert. So this stays OFF until
// RTNODE_VBAT_ADC_PIN is defined for the target board (verified on the bench);
// until then battery reads "not reported" and the beacon simply omits it —
// honest, never guessed.
//
// Heltec reference to CONFIRM before enabling (per-board, do not assume V4==V3):
//   Heltec V3: ADC on GPIO1, enable the divider by driving ADC_Ctrl (GPIO37)
//              LOW during the read, resistor divider ratio ≈ 4.9.
// Define these (e.g. in Config.h or a build flag) once measured on the bench:
//   #define RTNODE_VBAT_ADC_PIN   1
//   #define RTNODE_VBAT_CTRL_PIN  37     // optional: divider-enable pin
//   #define RTNODE_VBAT_DIVIDER   4.9f
inline uint16_t health_read_battery_mv() {
#ifdef RTNODE_VBAT_ADC_PIN
  #ifdef RTNODE_VBAT_CTRL_PIN
    pinMode(RTNODE_VBAT_CTRL_PIN, OUTPUT);
    digitalWrite(RTNODE_VBAT_CTRL_PIN, LOW);   // enable the resistor divider
    delay(10);
  #endif
    uint32_t pin_mv = analogReadMilliVolts(RTNODE_VBAT_ADC_PIN);
  #ifdef RTNODE_VBAT_CTRL_PIN
    digitalWrite(RTNODE_VBAT_CTRL_PIN, HIGH);  // disable divider to save power
  #endif
    uint32_t mv = (uint32_t)(pin_mv * (float)RTNODE_VBAT_DIVIDER);
    return (mv > 0xFFFF) ? 0xFFFF : (uint16_t)mv;
#else
    return HB_BATTERY_MV_UNKNOWN;   // no verified VBAT path -> not reported
#endif
}

// Single-cell Li-ion state-of-charge (%) from pack millivolts — a rough OCV
// curve mirroring reticulum-tool monitor/ups.py so both ends agree. 0xFF when
// there is no battery reading.
inline uint8_t health_battery_percent(uint16_t mv) {
    if (mv == HB_BATTERY_MV_UNKNOWN) return HB_BATTERY_PCT_UNKNOWN;
    float v = mv / 1000.0f;
    static const float bp_v[]  = {3.00f,3.30f,3.45f,3.55f,3.65f,3.75f,3.85f,3.95f,4.05f,4.15f,4.20f};
    static const int   bp_pct[]= {0,    8,    15,   25,   40,   55,   70,   82,   92,   98,   100};
    const int N = 11;
    if (v <= bp_v[0])   return 0;
    if (v >= bp_v[N-1]) return 100;
    for (int i = 1; i < N; i++) {
        if (v <= bp_v[i]) {
            float f = (v - bp_v[i-1]) / (bp_v[i] - bp_v[i-1]);
            return (uint8_t)(bp_pct[i-1] + f * (bp_pct[i] - bp_pct[i-1]));
        }
    }
    return 100;
}

// ─── Collection ─────────────────────────────────────────────────────────────
// Reads current health from existing globals. Cheap — safe to call per beacon
// or per HTTP request.
inline void collect_health(HealthSnapshot& h) {
    h.fork       = "RTNode";
    h.fw_version = RTNODE_FORK_VERSION;
#if defined(MAJ_VERS) && defined(MIN_VERS)
    h.rnode_maj  = MAJ_VERS;
    h.rnode_min  = MIN_VERS;
#else
    h.rnode_maj  = 0;
    h.rnode_min  = 0;
#endif

    h.board_model = BOARD_MODEL;
    h.board_name  = health_board_name();
#if defined(ESP32)
    h.psram_size  = ESP.getPsramSize();
    h.psram       = (h.psram_size > 0);
#else
    h.psram_size  = 0;
    h.psram       = false;
#endif

    h.uptime_ms    = millis();
    h.reset_reason = health_reset_reason_str();

#if defined(ESP32)
    h.heap_internal_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    h.heap_internal_min  = heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL);
    h.heap_total_free    = ESP.getFreeHeap();
#else
    // nRF52 (Adafruit core): dbgHeapFree() is the same call microReticulum's
    // allocator sizing uses. No low-water tracking exists here, so min is
    // reported as the CURRENT free -- an optimistic bound the tool already
    // treats as advisory, not a fabricated history.
    h.heap_internal_free = dbgHeapFree();
    h.heap_internal_min  = h.heap_internal_free;
    h.heap_total_free    = h.heap_internal_free;
#endif

    // The task watchdog is armed unconditionally at boot on both MCUs
    // (esp_task_wdt_init on ESP32; NRF_WDT block in setup() on nRF52).
    h.wdt_armed     = true;
#ifdef WDT_TIMEOUT
    h.wdt_timeout_s = WDT_TIMEOUT;
#else
    h.wdt_timeout_s = 0;
#endif

#if defined(ESP32)
    h.wifi_connected = (WiFi.status() == WL_CONNECTED);
    h.wifi_rssi      = h.wifi_connected ? WiFi.RSSI() : 0;
    h.wifi_ip        = WiFi.localIP();
#else
    h.wifi_connected = false;            // no WiFi radio on this chip
    h.wifi_rssi      = 0;
#endif

    h.lora_online                 = radio_online;
#ifdef FIREWALL_MODE
    h.tcp_backbone_connected      = firewall_state.tcp_connected;
    h.local_tcp_server_up         = health_local_server_up();
    h.local_tcp_client_connected  = firewall_state.ap_tcp_connected;
    h.node_name = firewall_state.node_name;
#else
    h.tcp_backbone_connected      = false;
    h.local_tcp_server_up         = false;
    h.local_tcp_client_connected  = false;
    h.node_name = "";
#endif

    // Power (v2 beacon). PRIMARY source: the firmware's own PMU path (Power.h)
    // — loop() -> update_pmu() -> measure_battery() maintains these globals
    // with the vendor-verified per-board pins (Heltec V4: pin_vbat=1,
    // pin_ctrl=37) and charge-state detection. No guessed pins, no second ADC
    // path. battery_installed goes true on the first valid sample, so a board
    // with no battery honestly reports "not reported".
    if (battery_installed && battery_voltage > 0.1f) {
        float mv = battery_voltage * 1000.0f;
        h.battery_mv  = (mv > 65535.0f) ? 65535 : (uint16_t)mv;
        float pct = battery_percent;
        if (pct < 0.0f) pct = 0.0f;
        if (pct > 100.0f) pct = 100.0f;
        h.battery_pct = (uint8_t)(pct + 0.5f);
        h.on_battery  = true;
        // CHARGING and CHARGED both mean "on external power, not running
        // down" — either way a low battery must not raise a battery alert.
        h.charging    = (battery_state == 0x02 /*CHARGING*/
                         || battery_state == 0x03 /*CHARGED*/);
    } else {
        // FALLBACK for boards without a PMU path: the explicit board-gated
        // ADC read (inert until RTNODE_VBAT_ADC_PIN is bench-verified).
        h.battery_mv  = health_read_battery_mv();
        h.battery_pct = health_battery_percent(h.battery_mv);
        h.on_battery  = (h.battery_mv != HB_BATTERY_MV_UNKNOWN);
        h.charging    = false;
    }
#ifdef RTNODE_POWER_SOLAR
    h.on_solar = true;  h.on_mains = false;   // build flag: solar-powered node
#else
    h.on_solar = false; h.on_mains = false;
#endif
    // The node's own LoRa link view — wire to the radio's last-RX SNR/RSSI at
    // the bench; unknown for now (the medic also measures the announce RSSI).
    h.lora_snr_db   = HB_LORA_LINK_UNKNOWN;
    h.lora_rssi_dbm = HB_LORA_LINK_UNKNOWN;
}

// ─── JSON serialization ─────────────────────────────────────────────────────
// SD overflow tier accessors (implemented in FileSystem.cpp) — forward-declared
// so this header needn't pull in the whole FileSystem/microReticulum include set.
bool     fs_sd_overflow_ready();
uint64_t fs_sd_card_size_bytes();
uint64_t fs_sd_used_bytes();
String   fs_sd_overflow_listing();

inline String health_to_json(const HealthSnapshot& h) {
    String j;
    j.reserve(896);
    j += "{";
    j += "\"fork\":\"";        j += h.fork;        j += "\",";
    j += "\"fw_version\":\"";  j += h.fw_version;  j += "\",";
    j += "\"rnode_proto\":\""; j += h.rnode_maj; j += "."; j += h.rnode_min; j += "\",";

    j += "\"board_model\":"; j += h.board_model; j += ",";
    j += "\"board\":\"";     j += h.board_name;  j += "\",";
    j += "\"psram\":";       j += (h.psram ? "true" : "false"); j += ",";
    j += "\"psram_size\":";  j += h.psram_size;  j += ",";

    j += "\"uptime_ms\":";    j += h.uptime_ms;    j += ",";
    j += "\"reset_reason\":\""; j += h.reset_reason; j += "\",";

    j += "\"heap_internal_free\":"; j += h.heap_internal_free; j += ",";
    j += "\"heap_internal_min\":";  j += h.heap_internal_min;  j += ",";
    j += "\"heap_total_free\":";    j += h.heap_total_free;    j += ",";

    j += "\"wdt_armed\":";     j += (h.wdt_armed ? "true" : "false"); j += ",";
    j += "\"wdt_timeout_s\":"; j += h.wdt_timeout_s; j += ",";

    j += "\"wifi_connected\":"; j += (h.wifi_connected ? "true" : "false"); j += ",";
    j += "\"wifi_rssi\":";      j += h.wifi_rssi; j += ",";
#if defined(ESP32)
    j += "\"wifi_ip\":\"";      j += h.wifi_ip.toString(); j += "\",";
#endif

    j += "\"lora_online\":";                j += (h.lora_online ? "true" : "false"); j += ",";
    j += "\"tcp_backbone_connected\":";     j += (h.tcp_backbone_connected ? "true" : "false"); j += ",";
    j += "\"local_tcp_server_up\":";        j += (h.local_tcp_server_up ? "true" : "false"); j += ",";
    j += "\"local_tcp_client_connected\":"; j += (h.local_tcp_client_connected ? "true" : "false"); j += ",";

    j += "\"node_name\":\""; j += (h.node_name ? h.node_name : ""); j += "\",";

    // SD overflow tier — confirms remotely that the path table + cache live on
    // the microSD card. mounted:false on boards without the tier.
    j += "\"sd_overflow\":{\"mounted\":";
    j += (fs_sd_overflow_ready() ? "true" : "false");
    if (fs_sd_overflow_ready()) {
        j += ",\"card_mb\":"; j += (uint32_t)(fs_sd_card_size_bytes() / (1024ULL * 1024ULL));
        j += ",\"used_kb\":"; j += (uint32_t)(fs_sd_used_bytes() / 1024ULL);
        j += ",\"files\":";   j += fs_sd_overflow_listing();
    }
    j += "},";

    // Reserved for Phase 2: boot-log FATAL/ERROR capture. Emitted now so the
    // Pi tool can rely on a stable schema.
    j += "\"faults\":[]";
    j += "}";
    return j;
}

// ─── STA-mode status web server ─────────────────────────────────────────────
// WiFi-only by nature (WebServer + firewall_state); a chip with no WiFi
// radio serves its health over the LoRa beacon instead.
#if defined(ESP32) && defined(FIREWALL_MODE)
static WebServer* health_server        = nullptr;
static bool       health_server_started = false;

inline void health_handle_status() {
    HealthSnapshot h;
    collect_health(h);
    health_server->sendHeader("Cache-Control", "no-store");
    health_server->send(200, "application/json", health_to_json(h));
    // NO LED here (operator decision 2026-07-31): the medic polls /status
    // routinely (~every 30 s), and a green blink per poll floods the LED
    // language until people ignore it. The pixel speaks only for RX, TX,
    // fault, and a COMMANDED health check (the LoRa 0x01 double-pulse) —
    // deliberate signals, each worth looking up for.
}

// Idempotent: starts the server the first time WiFi station mode is up, and
// no-ops thereafter. Safe to call every loop iteration.
inline void health_server_ensure_started() {
    if (health_server_started) return;
    if (!(firewall_state.wifi_enabled && WiFi.status() == WL_CONNECTED)) return;

    if (health_server == nullptr) {
        health_server = new WebServer(RTNODE_HEALTH_PORT);
        health_server->on("/status", HTTP_GET, health_handle_status);
    }
    health_server->begin();
    health_server_started = true;
    Serial.printf("[Health] Status endpoint up: http://%s/status\r\n",
                  WiFi.localIP().toString().c_str());
}

inline void health_server_loop() {
    if (health_server_started && health_server != nullptr) {
        health_server->handleClient();
    }
}
#endif // ESP32 && FIREWALL_MODE

#endif // HEALTHSTATUS_H
