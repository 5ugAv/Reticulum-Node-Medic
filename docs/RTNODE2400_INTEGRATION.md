# RTNode‑2400 ↔ Reticulum‑Node‑Medic — integration facts

Answers to the tool‑side checklist. Verified against firmware branch `feature/neopixel-status-led`
(`github.com/5ugAv/RTNode-2400`), with file:line refs.

---

## A. Captive‑portal HTTP contract

**1. Endpoint + method** — `GET /` serves the form; the form submits **`POST /save`**. Config web server listens on **TCP port 80** (`HTTP_PORT`, FirewallConfig.h:40; routes at :843‑844).

**2. Content type** — `application/x-www-form-urlencoded` (plain HTML `<form method='POST' action='/save'>`, FirewallConfig.h:157). There is **no JSON endpoint**.

**3. Field names (POST `/save`)** —
`node_name`, `mdns_en`, `mdns_name`, `ssid`, `psk`, `wifi_en`, `tcp_mode`, `tcp_port`, `bb_host`, `bb_port`, `ap_tcp_en`, `ap_tcp_port`, `freq`, `bw`, `sf`, `cr`, `txp`, `ifac_en`, `ifac_name`, `ifac_pass`, `advert_en`, `advert_lat`, `advert_lon`, `advert_jitter`, `disp_blank`, `disp_rot`, `stal`, `ltal`.
**Boundary/role:** there is **no runtime boundary/role field**. The LAN↔WAN boundary is a *compile‑time* flag (`-DFIREWALL_MODE`, `-DFIREWALL_TCP_MODE=0`) baked into the `heltec_V4_boundary-local` build. The closest runtime controls are `tcp_mode` (0 = backbone disabled, 1 = client) + `bb_host`/`bb_port`. This build defaults `tcp_mode=0` (LoRa‑only, no WAN backbone).

**4. Units** —
- `freq`: **MHz, decimal string** → ×1e6 internally. e.g. `915.125` (FirewallConfig.h:688‑691).
- `bw`: **Hz, integer** → assigned directly. e.g. `125000` (= 125 kHz) (FirewallConfig.h:694‑696).
- `sf`: int 5–12 · `cr`: int 5–8 · `txp`: int dBm 2–30 (FirewallConfig.h:698‑705).

**5. Required / optional + validation** — `ssid`/`psk` needed only if `wifi_en=1` (else LoRa‑only). LoRa fields are range‑checked and applied only if valid (`sf` 5‑12, `cr` 5‑8, `txp` 2‑30, `freq`>0, `bw`>0); out‑of‑range/empty → keeps the existing/default value. So a partial POST is fine — unspecified LoRa fields keep defaults. Blank `mdns_name` → auto `rtnodeXXXX`.

**6. Success signal** — HTTP **200** with an HTML page containing *"Device will reboot in 3 seconds and connect to your WiFi network."* (FirewallConfig.h:760), then **`ESP.restart()`** ~3 s later (:770). **Not** JSON, **not** a redirect. After reboot: rejoins WiFi, RNS comes up, first health beacon at **~30 s** post‑boot (see #16).

**7. AP details** — SSID **`RTNode-Setup`**, **OPEN (no password)** — `WiFi.softAP(SSID, NULL)` (FirewallConfig.h:825). AP IP + gateway **10.0.0.1**, mask 255.255.255.0 (`softAPConfig`, :828‑830). DHCP: ESP‑IDF default softAP pool — client leases start at **10.0.0.2** upward; the Pi gets a 10.0.0.x and reaches 10.0.0.1. Config server on TCP 80.

---

## B. Serial log / KISS surface

Serial/USB is **native USB CDC** (`ARDUINO_USB_CDC_ON_BOOT=1`). **Baud 115200** (Config.h:76) *(#9)*.

**8. Beacon line — exact:**
> ⚠ **Real‑hardware correction (validated on board "TRUTH"):** this line is emitted **only once the board is running normally (post‑onboarding)**. A fresh / un‑onboarded board is **silent on USB** — it blocks in the captive portal before the beacon code ever runs (see #16). Don't expect any `[HealthBeacon]` output until after `wifi_onboarding`.
```
[HealthBeacon] announce dst=<hash> data=<payload>\r\n        (HealthBeacon.h:106)
```
- `dst` = destination hash, `toHex()` = **32 lowercase hex** chars (16 bytes).
- `data` = beacon payload, `toHex()` = **28 lowercase hex** chars (14 bytes; `HEALTH_BEACON_LEN=14`).
- Raw `Serial.printf` — **no timestamp/log‑level prefix** on this line. (Reticulum's own `info/VERBOSE` lines elsewhere may carry prefixes; the `[HealthBeacon]` lines are bare.)
- Related lines: `[HealthBeacon] init dst=<hash>, first announce in ~30s` · `[HealthBeacon] on-demand poll request (0x01) -> announcing now` · `[HealthBeacon] FAULT confirmed after <n> strikes (heap=<u>) -> immediate beacon` · `[HealthBeacon] fault cleared (heap recovered)`.

**10. Other passive lines worth parsing (exact):**
- `[WATCHDOG] CRITICAL: Free heap <u> < <u> — REBOOTING` (RNode_Firmware.ino:2616) → boot_fatal / heap‑floor trigger.
- `[WATCHDOG] WiFi.status()=<d> heap=<u> min_heap=<u>` (:2646) → periodic heap + WiFi state.
- `[TcpIF] Client <d> <up/down> (heap: <u> -> <u>, delta: <+d>)` (TcpInterface.h:324).
- `[Health] Status endpoint up: http://<ip>/status` (HealthStatus.h:231).
- There is **no separate `mem_free:` line** — heap is reported via the `[WATCHDOG]` periodic line; key heap checks off that.

**11. KISS opcodes answered** (RNode host protocol, same USB serial): `CMD_FREQUENCY`, `CMD_BANDWIDTH`, `CMD_TXPOWER`, `CMD_SF`, `CMD_CR`, `CMD_RADIO_STATE`, `CMD_STAT_RX`, `CMD_STAT_TX`, `CMD_STAT_RSSI`, `CMD_RADIO_LOCK`, `CMD_DETECT`, `CMD_FW_VERSION`, `CMD_BOARD`, `CMD_DATA` (RNode_Firmware.ino:1649‑1880). So the tool **can read radio state directly via KISS** instead of waiting for a beacon.
⚠ The USB CDC stream carries **both** the human log lines above **and** KISS frames — a reader must frame on **FEND (0xC0)** and ignore non‑framed text. Confirm on a physical visit that the host protocol is live in this firewall build before relying on it; the beacon/serial‑log path is the guaranteed surface.

**12. Serial "dump health now"** — **not implemented.** On‑demand poll is LoRa‑only (1‑byte opcode `0x01` to the `rtnode.health` destination). A serial trigger is feasible but must not collide with KISS FEND framing. Recommendation: leave out for now unless you specifically want it.

---

## C. PlatformIO build

**13. Envs** — for Heltec V4 use **`heltec_V4_boundary-local`** (the buildable Type‑B env). `heltec_V4_boundary` exists but is missing the NeoPixel `lib_dep` — don't use it. Other envs exist for unrelated boards (xiao esp32s3, lilygo t3‑s3, t‑watch‑s3). No separate Heltec **V3** env in this line (V3 = board id 0x3A if added later).

**14. Deps to pre‑stage in `~/.platformio`** — platform `espressif32`; `framework-arduinoespressif32`; xtensa‑esp32s3 toolchain; `tool-esptoolpy`; `tool-mklittlefs`; and this env's `lib_deps`: **`XPowersLib@^0.2.1`**, **`adafruit/Adafruit NeoPixel@^1.12.0`** (microReticulum is vendored in‑tree under `lib/`). Exact resolved versions land after one online build — run `pio pkg list -e heltec_V4_boundary-local` and snapshot `~/.platformio/{platforms,packages}`; carry that cache for offline field builds. *(I can produce a pinned version manifest from a build if you want it.)*

**15. Filesystem image** — **No separate `uploadfs` needed.** `board_build.filesystem=littlefs`, partitions `default_16MB.csv`. The firmware mounts LittleFS (formatting if empty) and **self‑generates the Reticulum identity on first boot** — the app image brings the FS up empty and populates it. A first‑ever flash of just the app image is sufficient. (The identity is generated on the first *configured* boot; `verify_beacon` only sees a beacon **after onboarding** — see #16 — not right after flash.)

**16. First‑beacon timing** — **Corrected from real‑hardware validation.** A fresh / un‑onboarded board does **not** beacon. In `setup()`, with no saved config the firmware starts the captive portal and **blocks** — `config_portal_start(); while (config_portal_is_active()) config_portal_loop();` (RNode_Firmware.ino ~617‑643) — never reaching `health_beacon_init()` (line 1043) or `loop()`. So a just‑flashed board sits **silent** in the `RTNode-Setup` portal. Only after onboarding (config saved → reboot → portal skipped) does `health_beacon_init()` run, and the first beacon fires **~30 s after that (configured) boot** (`HEALTH_BEACON_INITIAL_DELAY_MS`). ➡ **Run `verify_beacon` *after* `wifi_onboarding`, not right after flash;** capture window ~45–60 s from that reboot. (The USB‑CDC 0‑byte silence on a fresh board is expected — blocked in portal, and early boot prints are lost during USB re‑enumeration.)

**17. Bootloader entry** — ESP32‑S3, native USB CDC. esptool/pio normally auto‑reset into download mode via DTR/RTS. If auto‑reset fails (common on native‑USB S3): hold **PRG (BOOT)**, tap **RST**, release RST, then release PRG → download mode; flash; tap **RST** to run. Good text for the recovery panel.

---

## D. Quick confirmations

**18. Brand‑new board** — confirmed: first flash → firmware self‑generates the LittleFS identity and beacons with a stable `dst` hash — **but only once onboarded** (a fresh board blocks in the portal first; see #16). Identity persists across `pio run -t upload`; only a full chip erase rotates it.

**19. Fault bit (b6) after the debounce commit** — heap‑pressure early warning. Threshold `HEALTH_FAULT_HEAP_KB` = **40 KB** free *internal* SRAM (`MALLOC_CAP_INTERNAL`), checked every **30 s** (`HEALTH_FAULT_CHECK_INTERVAL_MS`). Fault **confirms after 3 consecutive** sub‑40 KB checks (`HEALTH_FAULT_STRIKES=3` ≈ 90 s sustained), sets b6=1, and fires an immediate beacon on the false→true edge. Clears (b6=0) at the first check where heap recovers ≥ 40 KB. So `heap_fault` = "internal free heap under 40 KB for ~90 s"; `heap_low` messaging should mirror the 40 KB floor.

**20. Board‑id byte** — `BOARD_HELTEC32_V4` = **0x3F** (Boards.h:106). V3 = 0x3A if you later support it.

---

*Priority items per the checklist: #1–4 fully answered; #14–15 answered (no `uploadfs`; carry the `~/.platformio` cache — pinned versions available on request).*
