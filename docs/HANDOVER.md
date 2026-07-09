# Reticulum-Node-Medic — Project Handover

*A portable Raspberry Pi 5 field tool for provisioning, diagnosing, repairing
and monitoring Reticulum mesh nodes. This document is the running context so any
collaborator or future session can continue without the original chat.*

Repo: `github.com/5ugAv/Reticulum-Node-Medic` · `main` · public · MIT · CI green.

---

## TL;DR
Built from an empty repo to a full, tested codebase: **382 passing tests**, all
five operating modes represented, both RTNode-2400 firmware contracts negotiated
and locked in code, a real board flashed, and a bug-hunt pass done.

## Environment / how to run
- Working dir: `~/reticulum-tool` (== the repo). Python 3.14.6, pytest 9.1.1,
  kivy 2.3.1.
- **Run the suite:** `python3 -m pytest` → 382 passing. The tested core imports
  **no third-party deps** (Kivy is UI-only and not imported by the suite); CI
  runs it on 3.11/3.12.
- **Headless-testable by design:** everything goes through a `Connection`
  abstraction with an `EmulatedConnection` (rule list, substring / `^`-prefix
  matching, first-match-wins). Every I/O seam is injected (GPS reader, HTTP
  POST, AP-join, SSH runner) so the backend is fully unit-tested without
  hardware or a display.
- **UI** (`ui/`) is Kivy and cannot run in the dev sandbox (no display / PIL
  text provider) — screens are compile-verified; their logic lives in the
  tested core.

## What's built (by area)
- `node_profile.py` — dataclasses / enums (foundation).
- `transport/connection.py` — `Connection` base, `SSHConnection` (retries
  transient 255s), `SerialConnection` (sentinel framing via `rfind`; base64
  file-push), `EmulatedConnection`, `auto_detect_connection`.
- `diagnostics/` — `base.py` (+ `_priv()` sudo-escalation, `_check`) and **7
  modules, 91 checks**, each with plain-English text, a severity, and an auto-fix
  where possible. Six **Pi** modules (Power / Reticulum-software / Radio /
  System-health / Network-mesh / Client); `rtnode_2400.py` is **beacon-driven**
  (Type-B boards have no text console — it parses the serial `[HealthBeacon]`
  line).
- `workflows/` — `build.py` (Pi, 10 steps), `rtnode_build.py` (Type-B, 5 steps +
  GPS capture), `repair.py` (chains the 6 Pi modules; progress events; fix-all),
  `clone.py` (Clone Tool — copies OS/assets/monitoring-DB, generates a *fresh*
  identity), `rtnode_portal.py` (captive-portal client + `onboard()`),
  `build_warnings.py`. Each workflow has its **own** step registry.
- `monitor/` — `health_beacon.py` (14-byte codec; `encode`/`decode`/`to_bytes`;
  two golden vectors), `health_poll.py` (on-demand poll with retries),
  `registry.py` (**Monitor backend**: node registry keyed by dst hash; ingest;
  status + 6 h-staleness→red; poll folding; JSON persistence = the monitoring
  DB; commissioning log; field notes; firmware tracking; location + navigation),
  `geo.py` (injectable GPS + nav links), `formatting.py`.
- `ui/` — `theme`, `safety` (board-specific abort recovery), widgets
  (`hex_status` hexagon, `stat_bar`, `sidebar`), screens (`monitor`, `repair`,
  `node_detail`, `build`), `app`.
- `assets/` — 4 Reticulum config templates; `scripts/flash_rtnode2400.sh`
  (carried, hardened) + `apply_neopixel_patch.py`.
- `docs/RTNODE2400_INTEGRATION.md` — firmware-authored contract answers (portal,
  beacon/KISS, build, fault semantics, Section E location). `.github/workflows/`.

---

## Cross-project contracts with `5ugAv/RTNode-2400` firmware — LOCKED
1. **Health beacon.** Type-B can't do LXMF (embedded C++ RNS is core-only).
   Health rides in the `app_data` of an RNS **announce** on aspect
   `rtnode.health` (SINGLE destination). Payload = **14 bytes, big-endian**,
   decoded by `monitor/health_beacon.py`. Two golden vectors are pinned as
   regression tests (spec + a real Heltec V4 capture
   `010000002400c7cc053b3f000602`). Cross-impl hash match verified on hardware.
2. **On-demand poll.** 1-byte opcode `0x01` to the same destination → immediate
   beacon; a clean reply clears a node's warning to green. Unknown opcodes are
   no-ops (forward-compatible).
3. **Captive-portal onboarding.** `POST /save` (form-urlencoded) at
   `http://10.0.0.1` (AP `RTNode-Setup`, open). Real field names/units wired:
   `freq` (MHz decimal string), `bw` (Hz int), `sf`/`cr`/`txp`, `ssid`/`psk`/
   `node_name`.
4. **Location (Section E).** One GPS read at flash time: the node advertises a
   firmware-**fuzzed ~800 m** public pin (`advert_en/lat/lon/jitter`, jitter ON
   by default) while the **exact** coords go on the birth certificate for repair
   visits; the registry stores them and `navigation()` yields Google/Apple
   directions links.

## RTNode-2400 firmware open issues (tracked in the `5ugAv/RTNode-2400` session)
These are **firmware-side**, not tool bugs, but they shape what the tool should
watch for:
- **Heap leak under persistent TCP connections** — not yet root-caused; heap
  telemetry exists in the firmware logs (and in the beacon: `free_heap_kb` +
  the `fault` bit, which trips at <40 KB internal SRAM sustained ~90 s). The
  tool surfaces this via `heap_low` / `heap_fault` / the beacon fault flag.
- **WiFi lockup under weak signal** — multi-subsystem stall (WiFi+BT+LoRa),
  root cause unknown; a hardware-watchdog fix is in progress. The tool watches
  `wifi_link` + `wifi_rssi` (warn ≤ −75, alert ≤ −85 dBm) and the beacon
  `wdt_armed` flag.
- **Hardware watchdog armed confirmation** — being investigated firmware-side.
  Until confirmed, the tool's `watchdog_armed` check (beacon bit b4) may report
  "not armed"; treat as informational until the firmware confirms.

## Hardware milestone
A physical Heltec V4 named **"TRUTH"** was flashed this session from the firmware
working tree (`pio run -e heltec_V4_boundary-local -t upload`), hash-verified.
Its USB serial was silent — **expected**: a fresh, un-onboarded board blocks in
the captive portal and does not beacon until configured (hence `verify_beacon`
runs *after* onboarding), plus an ESP32-S3 USB-CDC quirk. Not a fault.

## Key facts / decisions
- RTNode-2400 identity persists in **LittleFS** — survives `pio run -t upload`,
  rotates only on a full chip erase (the sole trigger for the tool's "re-bind
  hash to existing node").
- Board-id byte == RNode `BOARD_MODEL` (0x3F Heltec V4); the tool mirrors the
  full enum.
- Fault bit (b6) = internal free heap < 40 KB sustained ~90 s (3 strikes).
- The same diagnostic code runs in all three self-healing tiers; only the
  `Connection` differs.

---

## ⚠️ HIGHEST-RISK OPEN ITEM — validate Pi-module parsers against real output

Several **Pi** diagnostic checks parse command output whose format was authored
for the emulator, **not** verified against real tools. A parser that passes in
emulation but misreads real output is **worse than no check** — it gives false
confidence.

### PROGRESS (validated against real hardware / RNS 1.3.7)
- ✅ **PINNED & FIXED** (commit `113b098`): `radio_interface_up`, `path_table_populated`,
  `channel_congestion` — real `rnstatus`/`rnpath -t` output exposed three real
  bugs (radio "Up" matched anywhere while the RNode was Down; no "paths known"
  line exists — use `rnpath -t` "is N hop" entries; real label is `Ch. Load : X%`).
  `peers_heard` already matched.
- ✅ **COMMAND NAMES CONFIRMED** against real `--help`: `rnodeconf -i` == `--info`
  (both valid); `rnpath -t`, `rnstatus` correct.

### ⚠️ COMMAND-LEVEL BUGS FOUND (real RNS 1.3.7 help) — fix with hardware in the morning
These checks call commands/flags that **do not exist**, so they don't just
misparse — they error / silently pass on every real node:
- **`rnping` DOES NOT EXIST** in RNS 1.3.7 → `mesh_ping_l2` (41) fails
  "command not found". The real probe tool is **`rnprobe`** (`rnprobe [full_name]
  [destination_hash]`).
- **`rnodeconf` has NO `--loop` flag** → `radio_loopback` (21) and `loopback_l1`
  (40) error. Redefine L1 as "`rnodeconf <port> -i` responds" (real serial
  round-trip).
- **`rnodeconf --version` prints the PROGRAM version, not the device** →
  `serial_data_capable` (86) always passes (defeated). Use `-i` device response.
- **`rnprobe` needs a REAL destination** (name or hex hash) — the placeholder
  `"mesh-test"` won't resolve, so L2/L3 need a target strategy (e.g. a known peer
  from `rnpath -t`, or a profile-configured test destination).
- **Redundancy to resolve:** `serial_responsive` (12) ≈ `serial_data_capable`
  (86) ≈ `radio_loopback` (21) all really test "board responds to `-i`". Consider
  consolidating.
- **`rnodeconf --info` output format** is still UNVERIFIED (faith, the connected
  RTNode-2400, doesn't expose the RNode host protocol over USB in the boundary
  build — "RNode did not respond"; the `radio_firmware` frequency/bandwidth/etc.
  parsers need a capture from a board flashed with stock **RNode** firmware).

### STILL NEED real captures (need a reachable Pi node)
`chronyc tracking` (regex looks standard-correct but unverified), `journalctl -u
rnsd` (does rnsd log the word "announce"? what does a param-mismatch line say?),
and `rnodeconf <port> --info` from a stock-RNode board.

### ★ STRONGLY RECOMMENDED: switch rnstatus/rnpath checks to `--json`
`rnstatus` and `rnpath` both support `--json`, which is **far more robust than
scraping human text** (immune to spacing/wording changes across RNS versions).
Real schemas captured from RNS 1.3.7:

- `rnstatus --json` → `{"interfaces": [ {…}, … ]}`, each interface has:
  `name` ("RNodeInterface[RNode Interface]"), `type` ("RNodeInterface" /
  "AutoInterface" / "TCPClientInterface" / "LocalServerInterface"),
  **`status` (bool — Up=true/Down=false)**, `channel_load_short`,
  `channel_load_long`, `airtime_short`, `airtime_long`, `noise_floor`,
  `battery_percent`, `cpu_temp`, `interference`, `peers`, `mode`, `hash`, …
- `rnpath -t --json` → a **list** of `{"hash","via","hops","expires","interface"}`.

Recommended rewrites (all four network/radio-interface checks):
- `radio_interface_up`: interface with `type=="RNodeInterface"` and
  `status==true`. (Unambiguous — safe to switch now.)
- `peers_heard` / `path_table_populated`: `len(rnpath -t --json) > 0`.
  (Unambiguous — safe.)
- `channel_congestion`: use `channel_load_short`. **⚠ OPEN: confirm the scale** —
  is it a 0.0–1.0 fraction (then threshold `< 0.70`) or 0–100 (`< 70`)? My read
  of RNS is it's a **fraction** that rnstatus displays ×100 (the text showed
  "Ch. Load : 0.0%"), but the RNode was Down (load 0.0) so I couldn't confirm.
  Verify with an **Up** RNode under load before switching this one.

Bonus: the JSON also exposes `battery_percent`, `cpu_temp`, `noise_floor`,
`interference` per radio — these could feed real power/antenna checks instead of
the current sysfs/`vcgencmd` reads.

Caveat: `--json` needs the node's RNS to support it (1.x does; the mesh here
runs current tooling). The current **text** parsers are validated-correct
(commit `113b098`) and remain the safe default until the JSON switch is
confirmed.

---

The tables below are the remaining format assumptions to verify. For each: the
**exact command the tool runs**, and the **exact string / regex** each check
looks for.

For each command: the **exact command the tool runs**, and the **exact string /
regex** each check looks for. First thing to verify is the **command name/flags**
— if the command itself is wrong, the check silently gets empty output.

### 1. `rnstatus`  (tool runs: `rnstatus`, no args)
| Check | Module | Looks for |
|---|---|---|
| `radio_interface_up` | reticulum_software | substring `"Up"` in the output |
| `path_table_populated` | network_mesh | regex `(\d+)\s+paths known` |
| `channel_congestion` | network_mesh | regex `Channel load:\s*(\d+)%` |

⚠ Real `rnstatus` may not print the literal phrases "paths known" or
"Channel load: N%". Capture a live `rnsd` `rnstatus` and confirm/rewrite.

### 2. `rnodeconf <port> --info`  (tool runs `--info`; **you referenced `-i` — confirm the flag**)
Assumes an `--info` block containing these labelled lines:
| Check | Looks for |
|---|---|
| `firmware_present` | `"Firmware version"` |
| `firmware_hash_set` | `"Firmware hash"` |
| `firmware_version_current` | `"Firmware version: 1.80"` (⚠ `LATEST_FIRMWARE` = `1.80` — confirm the real current version) |
| `frequency` | `"915.125 MHz"` (`"{freq} MHz"`) |
| `bandwidth` | `"125.0 KHz"` (`"{bw} KHz"`) |
| `spreading_factor` | `"Spreading factor: 9"` |
| `coding_rate` | `"Coding rate: 5"` |
| `tx_power` | `"TX power: 17 dBm"` |
| `heltec_baud` | `"Serial baud rate: 115200"` |
| `antenna_rssi` | regex `Noise floor:\s*(-?\d+)` |
| `heltec_hw_revision` | `"Hardware revision"` |
| `flow_control_atmega` | `"ATmega"`, `"Flow control: enabled"` |

⚠ Real `rnodeconf --info` reports frequency/bandwidth differently (often Hz, and
different labels). This module is the **most format-sensitive** — capture a
provisioned Heltec V4's `rnodeconf <port> --info`.

### 3. `rnpath`  (tool runs: `rnpath -t`)
| Check | Looks for |
|---|---|
| `peers_heard` | non-empty output = at least one path/peer heard |

⚠ Confirm the flag (`-t` vs a table subcommand) and that a populated table is
non-empty text. Capture `rnpath` with real paths.

### 4. `chronyc tracking`  (tool runs: `chronyc tracking`)
| Check | Looks for |
|---|---|
| `clock_drift` | regex `System time\s*:\s*([\d.]+)\s*seconds` (drift ≥ 300 s → warn) |

⚠ chrony prints "System time : 0.000123 seconds slow of NTP time" — confirm the
exact spacing/wording. (`ntp_sync` separately uses
`timedatectl show -p NTPSynchronized --value` == `yes`.)

### 5. `journalctl -u rnsd`  (tool runs: `-n 200` and `-n 300`)
| Check | Module | Looks for |
|---|---|---|
| `announces_sending` | network_mesh | substring `"announce"` (lowercased) |
| `warm_boot_param_mismatch` | reticulum_software | substring `"mismatch"` (absence = healthy) |

⚠ Confirm rnsd actually logs the word "announce" in normal operation, and what a
real radio-param mismatch line says.

**Also worth a real check** (same class, lower risk): `rnping` (`"reply"`),
`rnprobe` (exit 0), `rnodeconf --loop` / `--version`,
`vcgencmd get_throttled` (`throttled=0x…`), `df --output=pcent`, `ss -tlnp`,
`getfacl`, `systemctl cat`. These are more standard but still assumed.

The RTNode-2400 module was already corrected this way once — its parsers were
pinned against the real `[HealthBeacon]` line and `[WATCHDOG] CRITICAL … REBOOTING`
format from a live board. Do the same for the five above and the Pi diagnostics
are trustworthy on real hardware.

---

## Backlog (not done)
- **Live `rnsd` wiring** — receiver logic done + tested (`registry.ingest_announce`);
  only `RNS.Transport.register_announce_handler(...)` in a running Reticulum
  instance remains (needs a live RNS + the tool's own radio).
- **Pi-module parser validation** — the highest-risk item above.
- **Map mode UI** — placeholder (needs carried offline map tiles).
- **Offline PlatformIO cache** — Type-B field builds need `~/.platformio` carried;
  firmware side to provide a pinned version manifest.
- `nmcli` AP-join tested on a real Pi; OTA push; commissioning-log UI polish;
  bundle an emoji font (currently short text labels instead).
- **Placeholder repo deletion** (firmware side) — needs a `delete_repo` token.

## Operating conventions (keep these)
- **Strict TDD**, suite green at every step; test count only rises (11 → 382).
- Every I/O seam injected for testability; each new workflow gets its own step
  registry.
- Commits are logical batches with clear messages; push via a transient git
  credential helper (never store the token in `.git/config`); revoke tokens
  after use.
- Reviews find bugs **and** fix them with regression tests.

## Suggested next move
Capture the five real command outputs (above), paste them in, and pin the Pi
parsers — that closes the biggest latent-correctness gap. Then wire the live
`rnsd` announce handler + Monitor dashboard on an actual Pi with a radio. Map
mode and the offline PlatformIO cache follow.
