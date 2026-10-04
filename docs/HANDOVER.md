# Node Medic — project reference

**This is the durable document.** What the tool is, how it is put together, the
contracts it must honour, and how to run and test it. Nothing here should go
stale in a week; if it does, it was written wrong.

Session state — what shipped last night, what is half-done, what to do next —
lives in the siblings below and is deliberately **not** repeated here.

| Read | For |
|---|---|
| `docs/HANDOVER_NEXT_SESSION.md` | Read this **first**. The goal, the operator, the offline roadblocks, current state, how instructions must be given. |
| `docs/WORKING_METHOD.md` | The rules that were paid for, each with the evening it cost. Part 5 is the previous assistant's own failures. Read before writing code. |
| `docs/NEXT_BRIEFS.md` | Six specified open jobs, A–F, ordered because they conflict. |
| **this file** | Architecture, the firmware contracts, the testing model, the reasons behind the design. |
| `README.md` | The feature tour — modes, screens, board catalogue, languages, current known gaps. Counts live there, not here. |
| `docs/history/SPEC.md` | The original specification (historical) and the "say only what you have checked" build rule. |
| `docs/RTNODE2400_INTEGRATION.md` | The firmware author's own answers, with `file:line` refs. The **source** for §4. |

Repo: `github.com/5ugAv/Reticulum-Node-Medic` · `main` · public · MIT.

---

## 1. What it is

A Raspberry Pi 5 touchscreen field tool that **builds, monitors, diagnoses and
clones** Reticulum LoRa mesh nodes. It is carried, not installed. The founding
principle, in the operator's words:

> Nodes stay useful and repairable when their keeper moves away or dies.

Everything follows from that. A node must be adoptable by someone who was not
there when it was built; the medic must be handable to someone else; a node in
the wild must not be traceable to a person or a place.

**And it must work with no internet.** Not "degrade gracefully" — *work*. The
mesh exists for places where infrastructure is absent. A tool that needs a
connection to build a node for a network whose purpose is not needing a
connection has missed its own point. Firmware, Python wheels, config templates,
board images and map tiles are all carried on disk.

### Node types

- **Type A — Raspberry Pi transport/propagation node.** `rnsd` + `lxmd`, with an
  attached RNode board as its radio.
- **Type B — standalone RTNode-2400.** ESP32 (Heltec V3/V4, T-Beam Supreme)
  running the 5ugAv microReticulum fork. No Pi. This is the firmware the
  contracts in §4 are with.
- **Type C — RNode only.** A LoRa32 board flashed with stock RNode firmware,
  acting as a Type A node's radio.

`node_profile.py` is the foundation module for all three: pure dataclasses and
enums, no I/O, no side effects. Everything else takes a `NodeProfile`.

### The radio invariant

| Frequency | Bandwidth | SF | Coding rate | TX power |
|---|---|---|---|---|
| 915.125 MHz | 125 kHz | 9 | 5 (4/5) | 17 dBm |

Regulatory basis: Australian LIPD Class Licence, 915 MHz band. These are the
defaults in `RadioConfig` (`node_profile.py`) and they are a **mesh-wide
invariant, not a preference** — nodes can only hear nodes on the same settings.
They are baked in at birth, not adjusted afterwards. Change the tool's defaults
and the home screen carries a badge until they are reverted.

---

## 2. Running it, testing it, and what this box cannot do

Verified 2026-08-15 in this worktree.

```bash
python3 -m pytest              # the whole suite, headless, no hardware
python3 main.py                # the touchscreen app (needs Kivy + a display)
python3 main.py --version
```

- **Python 3.14.6, pytest 9.1.1** on the dev machine; CI runs the matrix on
  **3.11 and 3.12** (`.github/workflows/`).
- **Suite: 3295 collected — 3284 passed, 11 skipped, ~2 min** (run in this
  worktree, 2026-08-15). Skips are toolchain/hardware gates. Counting rule
  inherited from day one: the number only goes up.
- **`pytest.ini` already sets `addopts = -q`.** Adding your own `-q` makes it
  `-qq`, which **eats the summary line** — you get progress dots, no
  "N passed". Use `-o addopts= -q` when you want the count.
- **The tested core imports no third-party runtime dependency.** Kivy is
  UI-only and the suite never imports it. CI installs `pytest`, plus **PyYAML
  and Pillow as test-only deps** — `test_cloud_init_seed` parses the imager's
  cloud-init independently instead of trusting the string it just built, and
  `test_terrain` builds real elevation tiles and reads a height back out, which
  is how the MBTiles row-flip got caught. Both were added after CI failed on
  import for every commit while passing locally, because dev machines had them
  and the runner did not.
- **The UI cannot run on a dev box** (no display, no PIL text provider). Screens
  are compile-verified; their logic lives in the tested core, which is the whole
  point of §3.
- **Emulated demos are opt-in.** On Linux — the deployed medic — flash, build,
  PROBE and Clone either do real work or fail with a stated reason. They never
  report a fake success. `RNM_DEMO=1` opts into emulated hardware and seeded demo
  nodes (`ui/hw_factories.py`, `ui/app.py`). SD imaging and both adoption paths
  are never emulated.
- **The medic's own runtime set** is pinned in `assets/requirements.txt` —
  `rns`, `lxmf`, `segno`, `kivy`, `adafruit-nrfutil` — and cached as wheels into
  `assets/packages/` by `workflows.wheelhouse`, so a clone installs the whole
  stack `--no-index`, offline. Each is imported lazily and behind a fallback:
  `ui/qr.py` returns `None` without segno so the caller prints a hint, the
  announce listener returns quietly with no RNS. **`pexpect` is used by
  `transport/connection.py` but is not in that file** — worth reconciling before
  a field clone needs the PTY flash path.
- **Deploy is rsync**, so `git log` on the medic reports the last commit and not
  what is running — use `git status --porcelain` there. The airlock hooks
  (`pre-commit`, `airlock-check.sh`, `deploy-medic.sh`) live under `.git/hooks/`
  of one checkout and are **not tracked** — a fresh clone has none of them
  (noted 2026-10-04). The tracked route is `README.md` ▸ *Updating the tool*:
  sync all of `git ls-files`, never a subset, then `scripts/restart_ui.sh`. See
  `WORKING_METHOD.md`; do not route around it.

---

## 3. How the code is arranged, and why

```
node_profile.py   dataclasses / enums — node roles, hardware, radio, connection
transport/        how a command reaches a target: SSH, Local, Serial, Emulated
diagnostics/      the check library: base classes + 8 category modules
workflows/        operations performed ON a node: build, flash, repair, adopt,
                  RTNode-2400 build, offline firmware/wheel caches, boards
monitor/          the observation layer behind VITALS/SCAN: beacon codec, poll,
                  registry, history, alerts, topology, placement, terrain,
                  location sharing, the medic's own self-diagnosis
provisioning/     the medic's OWN configuration and the link it uses to reach a
                  node: display, power, storage, clock, identity, vault, SSH
                  pinning, SD imaging, USB gadget, UART
ui/               the Kivy app — shell, theme, widgets, screens, plus non-Kivy
                  helpers (board detection, QR, i18n, geometry)
firmware/         vendored RTNode-2400 C++ headers, contract-tested against the
                  Python codec (§4.2)
sandbox/          a Lima VM mirroring the medic's software surface, so sudo
                  scoping, SSH hardening and the vault can be broken safely
scripts/          systemd units, boot/autostart installer, vault operator scripts
tests/            the suite, headless
assets/           Reticulum config templates, translation catalogues, board
                  photographs, UI artwork, carried firmware/wheels (gitignored)
```

### The `Connection` abstraction — the load-bearing decision

`transport/connection.py` defines one interface: `run(command) ->
(code, stdout, stderr)`, plus `push_file` / `push_tree`. Four implementations:

- **`SSHConnection`** — remote nodes. Retries transient 255s, pins host keys
  through `provisioning/host_keys`, prepends `~/.local/bin` to PATH so bare RNS
  console scripts resolve in a non-login shell.
- **`LocalConnection`** — the medic acting on a board on its own USB. Same PATH
  prepend. Also carries `run_interactive()`, a **pexpect PTY runner**, because
  `rnodeconf --autoinstall` reads its confirm prompts from a *terminal* and a
  piped run simply hangs. That runner sets `TERM=xterm` outright: it was
  inherited by luck before, so the same board flashed at 21:38 and failed at
  22:20 depending on how the UI had been launched (live, 2026-08-05).
- **`SerialConnection`** — a serial console has no clean framing, so each command
  is wrapped to echo a sentinel plus its exit code. It matches on the **last**
  occurrence (`rfind`): a console that echoes the command replays the wrapped
  line, sentinel and all, so the real completion marker is always the final one.
  A sentinel that never arrives returns `(-1, raw, "Sentinel not found …")`
  rather than hanging. Files go over base64.
- **`EmulatedConnection`** — an ordered rule list. **First match wins**; a
  pattern starting with `^` matches the start of the command, otherwise it
  matches anywhere, so register prefix rules before broader substring rules. An
  unmatched command returns **code 127 with "no emulator rule matched"** — a
  path nobody wrote a rule for fails as loudly as a missing binary rather than
  silently passing.

`auto_detect_connection(target)` picks Serial for anything under `/dev/`, SSH
otherwise.

**Why this shape.** The same diagnostic code has to run in all three
self-healing tiers — Tier 1 on the node under a systemd timer, Tier 2 remotely
from the medic, Tier 3 over a physical serial link with the stack down. Only the
`Connection` differs. The testability falls out of that for free: every I/O seam
(GPS reader, HTTP POST, AP-join, SSH runner, `nmcli`) is injected, so the entire
backend is unit-tested with no hardware and no display.

### How the medic learns anything (the monitor path)

`ui/app.py` starts two things at launch: a 30-second poll cycle (LAN `/status`
discovery, `rnpath` mesh discovery) and an **RNS announce listener** registered
against the shared `rnsd`. One handler ingests every announce into the persisted
registry; a second collects `rtnode.health` nodes as poll targets. With no RNS
library present — a dev box — the listener returns quietly and the rest of the
app runs.

Three properties of that path are load-bearing:

- **One machine, one row.** A device announces on several aspect destinations;
  the registry collapses them by announced *identity*, or VITALS shows the same
  node three times.
- **Every announce heard is logged, one line.** LoRa is quiet — a few an hour —
  so the log is affordable, and it is the ground truth for "is the app deaf?", a
  question that once took hours to answer. The registry only persists every 10th
  poll cycle (≈ 5 min — the SD card is not hammered every 30 s), so judge beacon
  arrival by the listener's log line, **never** by `registry.json` inside that
  window.
- **A cached Reticulum path is not a sighting.** Paths outlive the node by seven
  days. Presence in the path table proves nothing about now.

### Other structural rules

- **Checks never short-circuit.** `DiagnosticCheck.run()` executes every check
  in its category and returns one `Issue` per failure, so one fault cannot hide
  a later one. Fixes dispatch by check name through `_fix_handlers()`.
- **A fix is only "fixed" once the original check has been re-run and passes.**
  Not when the fix command returned 0.
- **Every workflow owns its step registry** (`_BUILD_STEPS` and the
  `@build_step` decorator in `workflows/build.py`; the same pattern in
  `rtnode_build.py`, `repair.py`). Steps are appended in definition order.
- **Time is passed in, never read.** `monitor/registry.py` takes epoch seconds
  as an argument so the backend is deterministic and testable.
- **A step that cannot fail is not a check.** Ask of every step: what would have
  to be true for this to report failure? If the answer is "nothing", it is
  decoration. This has bitten repeatedly — see `NEXT_BRIEFS.md` C.

---

## 4. The contracts with `5ugAv/RTNode-2400` — LOCKED

**This section is the most expensive knowledge in the repo.** It was negotiated
across two projects. Restructure it if you must; do not lose a field name or a
unit.

Provenance: the firmware-side answers were verified by the firmware author
against branch `feature/neopixel-status-led` with `file:line` refs, recorded in
`docs/RTNODE2400_INTEGRATION.md`. The tool-side values below were re-read from
this repo's source on 2026-08-15. Where the two are pinned to each other by a
test, that is said explicitly.

### 4.1 The health beacon

Type B boards **cannot run LXMF** — their embedded C++ Reticulum is core RNS
only. Health therefore rides in the `app_data` of a periodic RNS **announce** on
the aspect **`rtnode.health`** (a SINGLE destination). Both sides must build the
Destination with exactly that app_name + aspects or the hashes will not match.

The node's identity **is** the announce source hash. No node id is in the
payload; the medic maps the destination hash to a profile in its registry.

Payload, **big-endian**, decoded by `monitor/health_beacon.py`:

```
[0]      format version           0x01 = v1, 0x02 = v2
[1..4]   uptime seconds           uint32
[5..6]   free heap KB             uint16   (low-water mark preferred)
[7]      WiFi RSSI dBm            int8     (0 when WiFi is down)
[8]      reset reason             enum: 0 poweron, 1 panic, 2 brownout,
                                        3 task_wdt, 4 sw, 5 other
[9]      flags                    b0 wifi_up · b1 lora_up · b2 tcp_backbone_up
                                  b3 local_tcp_server_up · b4 wdt_armed
                                  b5 psram · b6 fault/breach · b7 airtime_lock
[10]     board id                 == RNode BOARD_MODEL (§4.6)
[11..13] firmware version         major, minor, patch
--- v2 tail, present only when [0] >= 0x02 ---------------------------------
[14..15] battery millivolts       uint16   (0 = not reported)
[16]     battery percent          uint8    (0xFF = unknown)
[17]     power flags              b0 on_battery · b1 charging · b2 on_solar
                                  b3 on_mains
[18]     LoRa link SNR dB         int8     (-128 = unknown) — the node's view
[19]     LoRa link RSSI dBm       int8     (-128 = unknown) — the node's view
```

`PAYLOAD_LEN = 14` (v1 / shared prefix), `PAYLOAD_LEN_V2 = 20`.

**Forward compatibility is by design.** `decode()` reads only the prefix a given
version defines and ignores trailing bytes, so a v1 tool reads a v2 beacon's
shared prefix and a v2 tool leaves the tail `None` on a v1 beacon. A future v3
can append again without a lockstep release. The v2 tail is gated on **length**,
not on the version byte, so a truncated payload can never over-read.

**The format byte is enforced, `0x01 <= b[0] <= 0x0F`.** Without that, anything
≥ 14 bytes decoded — so every LXMF phone announce on the mesh (msgpack, first
byte `0x9x`) landed on VITALS as a red-alerting phantom node. Seen live on the
operator's screen, 2026-08-13. The ceiling rejects text and msgpack while
leaving room for future versions.

Interpretation thresholds (`monitor/health_beacon.py`, all tool-side):

| Constant | Value | Meaning |
|---|---|---|
| `WIFI_WARN_DBM` | −75 | weak WiFi — **warn only, never alert** |
| `WIFI_ALERT_DBM` | −85 | retained as the "very weak" boundary; no longer escalates on its own |
| `BATTERY_WARN_PCT` | 30 | only when reported **and discharging** |
| `BATTERY_ALERT_PCT` | 12 | ditto |
| `LORA_SNR_WARN_DB` | −9 | marginal link — warn only |

Red is reserved for real problems: the fault flag, LoRa down, or a battery
critically low *and discharging*. A charging or mains node low on charge is
recovering, and a solar node dipping overnight is normal — the outage watch, not
the instant colour, decides whether it died. A healthy node that merely
associates at a weak RSSI must not go red; a false alarm next to a working
button teaches the operator to distrust the warnings that matter.

### 4.2 Golden vectors and the anti-drift test

`firmware/rtnode-2400/` vendors the firmware's own health-beacon headers —
`HealthBeaconPack.h` (pure `stdint`, no Arduino/RNS deps),
`HealthStatus.h`, `HealthBeacon.h`, `BirthCry.h` — so the wire format lives
beside its Python counterpart.

`tests/test_firmware_beacon_contract.py` **compiles `HealthBeaconPack.h` with
g++** and asserts its bytes equal `monitor.health_beacon.encode(...)` for both
v1 and v2. It skips cleanly where g++ is absent. This is the mechanism that
makes drift impossible rather than merely unlikely.

Two golden vectors are additionally pinned in `tests/test_health_beacon.py`:

- the **spec vector** `0100001C20008CC2003F3F000602` — uptime 7200 s, heap 140 KB,
  RSSI −62, reset poweron, flags b0–b5 set, board 0x3F, fw 0.6.2;
- a **real Heltec V4 capture** supplied by the firmware side,
  `010000002400c7cc053b3f000602` — uptime 36 s, heap 199 KB, RSSI −52, reset
  "other", fw 0.6.2. The `app_data` is the portable contract artifact and is
  untouched; the identity and destination hash that came with it were live-mesh
  addresses and were replaced with patterned synthetics.

Battery comes from the **firmware's own PMU path** — the existing
`battery_installed` / `battery_voltage` / `battery_percent` / `battery_state`
globals that `Power.h`'s `measure_battery()` maintains with vendor-verified
per-board pins (Heltec V4 and V3: `pin_vbat=1`, `pin_ctrl=37`). A board with no
battery honestly packs the "not reported" sentinels. **Never guess the pin** —
the ADC fallback stays inert until `RTNODE_VBAT_ADC_PIN` /
`RTNODE_VBAT_CTRL_PIN` / `RTNODE_VBAT_DIVIDER` are defined after bench
verification.

### 4.3 On-demand poll

A **1-byte opcode `0x01`** (`OPCODE_FULL_HEALTH`, `monitor/health_poll.py`) sent
to the same `rtnode.health` destination makes the node announce immediately. A
clean reply clears that node's warning back to green. **Unknown opcodes are
no-ops** on the firmware side, which is what makes the byte forward-compatible.

There is **no serial "dump health now"** trigger — the poll is LoRa-only, by
decision, because a serial trigger would collide with KISS FEND framing.

### 4.4 Captive-portal onboarding

- AP **`RTNode-Setup`**, **open, no password**. Gateway/AP IP **10.0.0.1**, mask
  255.255.255.0, DHCP pool from 10.0.0.2 up. Config server on **TCP 80**.
- The form is served at `GET /` and submits **`POST /save`** as
  **`application/x-www-form-urlencoded`**. There is **no JSON endpoint**.
- Success is **HTTP 200 with an HTML page** containing *"Device will reboot in 3
  seconds and connect to your WiFi network."* — not JSON, not a redirect —
  followed by `ESP.restart()` about 3 s later.

**Every accepted field:**

```
node_name  mdns_en  mdns_name  ssid  psk  wifi_en
tcp_mode  tcp_port  bb_host  bb_port  ap_tcp_en  ap_tcp_port
freq  bw  sf  cr  txp
ifac_en  ifac_name  ifac_pass
advert_en  advert_lat  advert_lon  advert_jitter
disp_blank  disp_rot  stal  ltal
```

**Units and ranges** — these are the part that silently breaks things:

| Field | Unit | Notes |
|---|---|---|
| `freq` | **MHz, decimal string** | e.g. `915.125`; multiplied by 1e6 in firmware |
| `bw` | **Hz, integer** | e.g. `125000` for 125 kHz. Not kHz. |
| `sf` | int 5–12 | |
| `cr` | int 5–8 | |
| `txp` | int dBm 2–30 | |

`ssid`/`psk` are needed only when `wifi_en=1`. LoRa fields are range-checked and
applied **only if valid** — out-of-range or empty keeps the existing value, so a
partial POST is safe. A blank `mdns_name` auto-generates `rtnodeXXXX`.

**There is no runtime boundary/role field.** The LAN↔WAN boundary is a
*compile-time* flag (`-DFIREWALL_MODE`, `-DFIREWALL_TCP_MODE=0`) baked into the
`heltec_V4_boundary-local` build. The nearest runtime controls are `tcp_mode`
(0 = backbone disabled, 1 = client) plus `bb_host`/`bb_port`.

What the medic actually posts (`workflows/rtnode_portal.build_form`) — the
project-standard shape, so every node comes out the same: `ap_tcp_en=1` and
`ap_tcp_port=4242` (this is how the monitor polls it and how another `rnsd`
connects to it), `mdns_en=1` with `mdns_name` unset so the firmware picks its
own, `tcp_mode=0` and `ifac_en=0` (standalone by design), and the five radio
fields from `RadioConfig`.

**First-beacon timing — the trap.** A fresh, un-onboarded board is **silent on
USB**. With no saved config the firmware starts the captive portal and *blocks*
in it, never reaching `health_beacon_init()` or `loop()`. Only after onboarding
(config saved → reboot → portal skipped) does the first beacon fire, **~30 s
after that configured boot**. So `verify_beacon` runs **after** `wifi_onboarding`,
never straight after flash, with a capture window of about 45–60 s. This was
corrected from real hardware; it was originally written the other way round and
read as a fault.

### 4.5 Location — the Section E contract

One GPS read at flash time. The node advertises a **fuzzed** public pin while the
**exact** coordinates stay on the medic's birth certificate for whoever has to
go and repair it.

| Field | Value | Notes |
|---|---|---|
| `advert_en` | `1` / `0` | enable device advertisement |
| `advert_lat` | signed decimal degrees | e.g. `-37.814000` — N/E positive, S/W negative. Blank = omit. |
| `advert_lon` | signed decimal degrees | e.g. `144.963000` |
| `advert_jitter` | `1` / `0` | firmware privacy offset on/off |

- **Firmware jitter** is a deterministic offset up to **~800 m**
  (`ADV_JITTER_RADIUS_METERS`), seeded by the node's own hash so the fuzzed pin
  sits in one stable spot rather than wandering between announces. It is a
  firmware constant, **not** a per-node field.
- **The medic fuzzes first, independently** (`monitor.geo.fuzz_location`,
  `FUZZ_RADIUS_M = 800.0`, seeded on the node name or identity hash). The
  `/save` POST crosses an **open WiFi AP as cleartext HTTP** and the node stores
  what it receives in flash, so the exact fix must never be sent. Before the
  2026-08-01 stranger's-eye audit the tool shipped 6-decimal (~0.1 m)
  coordinates and delegated all privacy to an unverified firmware constant.
- Both offsets are **deterministic on purpose**. An offset re-rolled per announce
  could be averaged back to the truth by a patient observer.
- The seed must stay stable for a second reason: upstream seals the discovery
  announce with an **LXMF proof-of-work stamp, cost 14** (matching
  `RNS/Discovery.py`'s `DEFAULT_STAMP_VALUE`) and caches it, redoing the work
  only when advertised parameters change. Renaming a node therefore genuinely
  moves its public pin and costs a fresh stamp — a real consequence, not an
  accident.
- With no fix or no confirmation, advertisement is left **off** — never `0,0`.
- **Default is silence.** `NodeProfile.share_location` rests at `"hidden"`, and
  `location_share.normalise()` maps anything unrecognised back to hidden: an
  unreadable setting must never be read as consent to publish.

**The announce mechanism, for both node classes.** There is exactly one way a
Reticulum node's position reaches a public map, and it is neither LXMF telemetry
nor ordinary announce app_data: **RNS interface discovery**, on the destination
aspects `rnstransport.discovery.interface`. On a Pi node that is a handful of
config keys inside the interface's own stanza (`discoverable`, `discovery_name`,
`announce_interval` in **minutes**, `latitude`, `longitude`, `height`); on an
RTNode-2400 the firmware emits the same announce from the four `advert_*` fields.
Two node classes, one wire format. Cadence: one announce on enable/boot, then
about every 6 h for LoRa airtime. Map pins reading "stale" between announces is
expected, not a fault.

The payload is `bytes([flags]) || umsgpack(info) || stamp`, where *info* is an
integer-keyed map. Read out of the RNS the medic itself runs (1.3.7,
`RNS/Discovery.py`, 2026-08-11): `LATITUDE = 0x03`, `LONGITUDE = 0x04`,
`HEIGHT = 0x05`, `TRANSPORT_ID = 0xFE`, `NAME = 0xFF`, `APP_NAME =
"rnstransport"`; `DISCOVERABLE_INTERFACE_TYPES` includes `RNodeInterface`.
`announce_interval` is in **minutes** (×60, floor 5 min, default 6 h).

**UNVERIFIED, and it must stay that way on screen:** rmap.world's v4 map listens
for exactly this aspect and drops a node after 7 days without a fresh announce
(its own instructions, read 2026-08-11), but **this tool has never observed one
of our announces arrive there**, and RMAP v4 is beta by its authors' own
description. Nothing in the UI may tell an operator their node *is* on a map —
only what was configured, and when the node last announced. A LoRa-only node's
announce does not leave the mesh at all; reaching a public map needs an uplink,
which hands the node's public IP to a third party, which is why it lives behind
`rmap_uplink_block()` and is never implied by choosing to share a position.

**The trap that makes this more than a config write:** setting `discoverable` on
an interface that is not already gateway or access-point mode makes RNS silently
reassign it, and for an RNodeInterface it picks **ACCESS POINT** — which stops
it rebroadcasting other destinations' announces. On the relay this mesh routes
through, that trades the network for a dot on a website, quietly. The managed
block therefore writes **`mode = gateway`** explicitly.

**⚠ The fuzz is invertible, and this is the most urgent open item in the
project.** Both offsets are seeded on values the same announce publishes — the
destination hash in firmware, the node name on the medic — so anyone who has
read either public repo recovers the true point exactly. Treat real protection
as **0 m against an informed observer**. The operator's decision (2026-08-15) is
to stop describing the fuzz on screen and ask for a deliberate operator-chosen
offset instead. **Do not delete the fuzz machinery** — it is still correct
wherever the tool holds a position the operator did not choose, and a
secret-salt version would restore it as real protection. Full brief: `NEXT_BRIEFS.md` F.

### 4.6 Board id byte == RNode `BOARD_MODEL`

RTNode-2400 is a 5ugAv RNode fork, so tool and firmware share one enum. The tool
mirrors it in full (`monitor/health_beacon.BOARD_IDS`):

```
0x31 RNode v1      0x38 Heltec32 V2   0x3E XIAO S3        0x4B T-Watch S3 Plus
0x32 HMBRW         0x39 LoRa32 v1.0   0x3F Heltec32 V4    0x50 Generic nRF52
0x33 T-Beam        0x3A Heltec32 V3   0x40 RNode NG 2.0   0x51 RAK4631
0x34 Huzzah32      0x3B T-Deck        0x41 RNode NG 2.1   0x52 XIAO nRF
0x35 Generic ESP32 0x3C Heltec T114   0x42 T3S3
0x36 LoRa32 v2.0   0x3D T-Beam S v1   0x44 T-Echo
0x37 LoRa32 v2.1
```

Plus one **synthetic** id kept deliberately above the real RNode range so it can
never collide: **`0xA0` = RPi propagation**. A Pi+RNode node is not an RNode
board but still emits a health beacon — it runs full RNS in Python, not the C++
firmware — so it needs an id of its own.

### 4.7 Firmware-side facts worth not rediscovering

- **Identity persists in LittleFS.** It survives `pio run -t upload` and rotates
  **only on a full chip erase** — which is the sole trigger for the tool's
  "re-bind hash to existing node" path. The firmware self-generates it on the
  first *configured* boot; a first-ever flash of just the app image is enough,
  no separate `uploadfs`.
- **Fault bit (b6)** = internal free heap below **40 KB** (`MALLOC_CAP_INTERNAL`,
  `HEALTH_FAULT_HEAP_KB`), checked every **30 s**, confirmed after **3
  consecutive** strikes (≈ 90 s sustained), with an immediate beacon on the
  false→true edge. Clears at the first check where heap recovers to ≥ 40 KB.
  `heap_low` messaging must mirror the same 40 KB floor.
- **PlatformIO env: `heltec_V4_boundary-local`.** `heltec_V4_boundary` exists but
  is missing the NeoPixel `lib_dep` — do not use it. `lib_deps`:
  `XPowersLib@^0.2.1`, `adafruit/Adafruit NeoPixel@^1.12.0`; microReticulum is
  vendored in-tree. Filesystem `littlefs`, partitions `default_16MB.csv`.
- **Serial is native USB CDC at 115200**, and the stream carries **both** human
  log lines **and** KISS frames — a reader must frame on **FEND (0xC0)** and
  ignore unframed text.
- **Passive log lines worth parsing, exactly:**
  `[HealthBeacon] announce dst=<32 lowercase hex> data=<28 lowercase hex>` (bare,
  no timestamp or level prefix), `[WATCHDOG] CRITICAL: Free heap <u> < <u> —
  REBOOTING`, `[WATCHDOG] WiFi.status()=<d> heap=<u> min_heap=<u>` (there is **no**
  separate `mem_free:` line — key heap checks off the WATCHDOG periodic),
  `[TcpIF] Client <d> <up/down> (heap: …)`, `[Health] Status endpoint up:
  http://<ip>/status`.
- **Bootloader entry** when auto-reset fails on native-USB S3: hold PRG (BOOT),
  tap RST, release RST, then release PRG → download mode; flash; tap RST to run.
- **A rich `GET /status` JSON exists on port 80** (`faults[]`, `lora_online`,
  `wifi_rssi`, `tcp_backbone_connected`, `board_model`, watchdog/heap), consumed
  by `monitor/http_status.py`. Discovery markers are `"RTNode"` and `"RNM-Pi"`
  (`PI_FORK`) — the Pi health reporter serves the same shape.
- **A key the node did not send is not a key it set to false.** An RTNode-2400
  sends all its link fields every time; a Pi propagation node omits any link it
  could not read. Without guarding on "known", that silence arrived as `False` —
  drawn amber, sending an operator to fix something never measured. Unknown
  belongs in the same grey as everything else nobody asked about.

### 4.8 Firmware-side open issues (tracked in the RTNode-2400 project)

Not tool bugs, but they shape what the tool watches for. **UNVERIFIED as of this
document — none re-checked here:**

- **Heap leak under persistent TCP connections**, not root-caused. Surfaced via
  `heap_low` / `heap_fault` and the beacon fault flag.
- **WiFi lockup under weak signal** — a multi-subsystem stall (WiFi+BT+LoRa),
  cause unknown, hardware-watchdog fix in progress. The tool watches `wifi_link`,
  `wifi_rssi` and the beacon `wdt_armed` flag.
- **Watchdog-armed confirmation** still being investigated firmware-side. Until
  it is confirmed, treat a `watchdog_armed` "not armed" as informational.

---

## 5. Diagnostics, and what real hardware taught the parsers

`diagnostics/` holds 8 modules. Seven run against Pi nodes in
operator-visible repair order (`workflows/repair.MODULE_ORDER`):

**Power & hardware → Reticulum software → Radio & firmware → System health →
Network & mesh → Client connectivity → GNSS**

The eighth, `rtnode_2400.py`, covers Type B boards and is **beacon-driven**:
those boards have no text console, so on a physical visit the tool captures the
passive `[HealthBeacon]` serial line, decodes it with the shared codec and
derives its checks from the decoded fields plus a boot-log FATAL scan. Same wire
contract as over the mesh. Check counts per module are visible as
`self._check(...)` call sites; the README carries the current totals.

Each check has a plain-English description, a severity
(`critical` / `warning` / `info`) and an auto-fix where one exists. Source
comments number the checks (`# 41 L2 …`) — those numbers are the stable way to
refer to a check in a conversation.

### The parser incidents — why these are written in the code

A parser that passes in emulation but misreads real output is **worse than no
check**: it gives false confidence. Every item below was found by capturing real
command output and is now pinned with a regression test. They are recorded here
because the same class of mistake keeps being available.

- **`rnodeconf --info` labels are column-aligned with a space before the colon**
  (`Spreading factor : 11`). The original `Spreading factor: N` patterns
  false-positived on **every real device**. Patterns must allow `\s*:`.
- **There is no "Firmware hash" field.** The real one is
  `Device signature : Verified/Unverified`.
- **`rnodeconf` has no `--loop` and no `--version` device flag.** L1 loopback is
  redefined as "the board responded to `--info` with a populated block".
- **`rnodeconf` exits 0 even on "Could not open port"** — so exit status is not
  evidence. `_device_read()` gates on the info block actually being present;
  `bool(info)` is not enough, because error text is also truthy.
- **Decoy lines must be skipped**: `Frequency range : …` and `Max TX power : …`
  sit near the real values. Hence the `(?<!Max )` lookbehind.
- **Architectural: on a live node, `rnsd` holds the RNode serial port**, so
  `rnodeconf --info` cannot open it and would false-positive the radio as dead on
  every running node. `--info` is a **build-time / maintenance-mode** tool. Live
  radio state comes from `rnstatus --json`.
- **`rnstatus --json`** → `{"interfaces": [...], "rxb":…, "txb":…}`; each
  interface carries `name`, `type` (`RNodeInterface` / `AutoInterface` /
  `TCPClientInterface` / …), **`status` as a bool**, plus RNodeInterface-only
  fields (`channel_load_short/long`, `airtime_short/long`, `noise_floor`,
  `battery_percent`, `cpu_temp`, `interference`,
  `outgoing_announce_frequency`, `incoming_announce_frequency`) which are
  correctly absent on non-radio interfaces. `rnpath -t --json` returns a JSON
  list, `[]` when empty. JSON is used throughout in preference to scraping human
  text, which changes wording between RNS versions.
- **`channel_load_short` is a PERCENT (0–100), not a 0.0–1.0 fraction.** Verified
  live: human `rnstatus` prints `Ch. Load : 0.14%` while the JSON value is
  `0.14`, and a busy node read `18.66`. The old `load < 0.70` plus a `×100`
  display read a healthy node as **"675%"**.
- **`rnping` needs a real destination hash.** The old `mesh-test` placeholder
  never resolved, so L2 always false-failed. It now pings a peer taken from the
  path table, and only when the radio is up — a down radio is already reported by
  L1, and double-reporting one fault as three is its own kind of lie.
- **`LATEST_FIRMWARE = "1.86"`**, from a real capture (Heltec LoRa32).
- **`clock_drift`**: a stock Pi has no chrony (it uses systemd-timesyncd), so
  `chronyc tracking` was command-not-found. Falls back to
  `timedatectl -p NTPSynchronized`.
- **`serial_acl`**: `getfacl` needs the `acl` package, absent on a stock Pi. A
  missing `getfacl` is now treated as **unverifiable**, not as "no access" —
  "I could not check" is its own answer.
- **RNode serial port**: `/dev/ttyUSB0` is wrong for ESP32-S3 native-USB RNodes,
  which appear as **`/dev/ttyACM*`**. `workflows.build.detect_rnode_port()`
  prefers the stable `/dev/serial/by-id/` mapping and falls back to the first
  `ttyACM`/`ttyUSB`. It also filters out the medic's **own** board at every step:
  its USB id hints match Jonesey exactly, so an unfiltered first-match handed out
  the medic's own radio (verified live, 2026-08-01 — the PROBE mis-target).

**⚠ One live contradiction, unresolved.** `diagnostics/network_mesh.py` (check 37)
states, citing a live Pi, that *rnsd under systemd writes no `~/.reticulum/logfile`
at all* and logs to the journal — while `diagnostics/reticulum_software.py`
(check 50, `warm_boot_param_mismatch`) still reads `tail -n 300
~/.reticulum/logfile` on the strength of the opposite claim. If network_mesh is
right, check 50 reads an empty string on every real node and therefore **can
never fail**. Both comments cite hardware; they cannot both be current. Resolve
it against a live node before trusting either. **UNVERIFIED here** — neither was
re-checked in this session.

---

## 6. Invariants — things that must not quietly change

- **Say only what you have checked.** Nothing is stated unless it is true, and
  true means what the thing in front of you reported *now*. Full treatment in
  `SPEC.md` and `WORKING_METHOD.md` Part 1; it is a build rule, not a sentiment.
- **The honesty gate.** On the medic, real work or an honest failure. Never an
  emulated success dressed as a real one.
- **Location default is silence**, and an unreadable setting is not consent.
- **An ambiguous flash menu is refused, not guessed.** `rnodeconf` picks some
  boards' band menu by radio *chip* (SX1276 vs SX1262 variants sold under one
  name), which nothing on this side of the USB cable can see. Those boards
  refuse with that fact (`RNodeBoard.band_ambiguity`) rather than "not yet
  verified", because a wrong band choice writes a wrong model byte into a
  board's EEPROM. `tests/test_birth_matrix.py` sweeps every Pi × board pairing
  through the full build in the emulator — that sweep is what found a missing
  band map before it reached a bench.
- **The medic can never flash its own radio.** `ui/onboard_roster.assert_flashable()`
  is wired at the write boundaries; the medic carries a permanent RNode of its own
  and must not confuse it with a work board.
- **Read back every privileged or remote write.** A write that is not read back
  is a claim, not a fact.
- **When the tool and the hardware disagree, believe the hardware** — then find
  out why the tool was wrong. It is almost never where you first look.

---

## 7. Open work

The specified jobs are **A–F in `docs/NEXT_BRIEFS.md`**, ordered because they
touch the same files. F (the location fuzz, §4.5) is the most urgent: it is the
only place the tool currently tells an operator something false about their own
safety.

Current state and the deploy/verify mechanics are in
`docs/HANDOVER_NEXT_SESSION.md` (rewritten 2026-10-04). Known gaps are
summarised in `README.md` under *Status* and itemised, with status, in
`docs/READINESS_LEDGER.md`.

Carried forward as **UNVERIFIED** in this document: §4.8 (the three firmware-side
issues) and the log-source contradiction at the end of §5.

**README counts** were recomputed on 2026-10-04 and the README now lists, under
*Counts*, the exact commands that produced each number. Re-run those rather
than copying a second set into this file — the 2026-08-15 table that used to
sit here is how a document rots.

---

## 8. Conventions

- **Test-first, always.** Write the test, watch it fail for the right reason,
  implement, run the **whole** suite. The count only goes up. A test that a step
  reports success is not a test that it did anything — test that each step **can
  fail**.
- **Comments carry the incident.** Prose in this repo explains *why*, and names
  the date and the symptom where there was one. That is what makes a comment
  survive a rewrite by someone who was not there.
- **Commits**: lowercase area prefix, blank line, then prose explaining why —
  `diagnostics: …`, `monitor: …`, `docs: …`. Logical batches, not dumps.
- **Never store a token in `.git/config`**; push through a transient credential
  helper and revoke afterwards. Check commit **metadata** (author/committer), not
  just diffs, before anything leaves the machine.
- **Never restart the medic's UI while it is working.** `scripts/restart_ui.sh`
  refuses during a flash or an SD write; a manual `pkill` does not.
