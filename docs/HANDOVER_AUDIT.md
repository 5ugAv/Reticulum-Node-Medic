# HANDOVER.md — staleness audit

*What in `docs/HANDOVER.md` is no longer true, with evidence. This is a
verification pass only; it does not rewrite the handover.*

**Baseline for every verdict below:** HEAD `5d9c24a` (2026-08-15), 738 commits.
Suite measured, not assumed: `python3 -m pytest` → **3284 passed, 11 skipped**
(3295 collected across 200 files in `tests/`) in 128 s. Python 3.14.6, pytest 9.1.1, kivy 2.3.1.

`docs/HANDOVER.md` was last edited in content by `e3eb3ac` (2026-07-14). The
project has since grown a successor, `docs/HANDOVER_NEXT_SESSION.md`
(2026-08-15), which points *back* at HANDOVER.md as "still the best reference
for what was agreed with the firmware side" — which is why its stale parts
matter: they are being actively recommended to newcomers.

**Headline:** the firmware contracts (Section "Cross-project contracts", lines
68-86) are the part that has held up best. The engineering-status parts — test
counts, module inventory, the whole HIGHEST-RISK section, the backlog — are
mostly obsolete, and five specific claims will actively waste a newcomer's day.

---

## Part 1 — Claims that send a reader down a path that no longer exists

These are worse than stale. A newcomer who believes them does work that cannot
succeed, or re-does work already finished.

| # | Claim (HANDOVER.md line) | Verdict | What is actually the case |
|---|---|---|---|
| 1.1 | Three commit hashes cited as the evidence trail: `113b098` (L131), `973a3e0` (L163), `4885a99` (L168) | **FALSE (dead references)** | The repo history was **rewritten for privacy on 2026-08-11**. None of the three is an ancestor of HEAD (`git merge-base --is-ancestor` → 1 for all three). They survive in this clone only via the backup branches `pre-rewrite-20260811` and `backup/pre-privacy-rewrite` (and `refs/original/refs/heads/main`). **A fresh clone from GitHub cannot resolve any of them.** Post-rewrite equivalents: `113b098`→`3065852`, `973a3e0`→`dab041b`, `4885a99`→`584649a`. |
| 1.2 | The entire **"⚠️ HIGHEST-RISK OPEN ITEM"** section (L122-297) presented as current work, ending "Suggested next move: capture the five real command outputs… that closes the biggest latent-correctness gap" (L356-360) | **STALE — ~85% of it is done** | The captures were taken and the parsers pinned. Of the five open items: `--loop` **fixed** (`diagnostics/radio_firmware.py:238-243`), `--version` **fixed** (`radio_firmware.py:285-291`), the `"mesh-test"` placeholder destination **fixed** (`diagnostics/network_mesh.py:88` now takes a real peer hash from `rnpath -t --json`), and the ★-recommended `--json` switch **done for all four checks** (`reticulum_software.py:81-87`, `network_mesh.py:38-43`, `:66-69`, `:76-80`, helpers at `diagnostics/base.py:164-191`). See 1.3 for the two that are genuinely still open. A newcomer following L356 would spend a day re-capturing output that is already pinned in `tests/test_diagnostic_network_mesh.py` and `tests/test_diagnostic_radio_firmware.py`. |
| 1.3 | Within that section: "**`rnping` DOES NOT EXIST** in RNS 1.3.7 → `mesh_ping_l2` (41) fails" (L141-143) | **STILL TRUE — and still unfixed** | `diagnostics/network_mesh.py:90` still runs `rnping {peer_hash}`. `rnprobe` appears nowhere in production code. The *destination* half of this bug was fixed; the *command name* half was not. The emulator rules in `tests/test_diagnostic_network_mesh.py:44,181` assert the literal `rnping`, so the suite is green either way. **This is the one item in the section that a newcomer should act on.** Likewise the "redundancy to resolve" note (L152-154) is still open: `serial_responsive` (`radio_firmware.py:137-140`) and `radio_loopback` (`:240-243`) are now *literally the same predicate* (`has_info`). |
| 1.4 | Section E / location: "the node advertises a **firmware-fuzzed ~800 m** public pin… while the exact coords go on the birth certificate" (L82-86) | **STALE MECHANISM — the medic no longer delegates privacy to the firmware** | A 2026-08-01 "stranger's-eye" audit found the tool was shipping 6-decimal (~0.1 m) coordinates over an **open AP as cleartext HTTP** and trusting an unverified firmware constant. The medic now fuzzes **before** the coordinates leave it: `workflows/rtnode_portal.py:104-131` calls `monitor.geo.fuzz_location` (deterministic, seeded by node name/identity) and sends the fuzzed pair as `advert_lat`/`advert_lon`. Two offsets now stack. A newcomer who believes L83 would conclude the medic transmits exact coordinates and that privacy is the firmware's job — the opposite of the current design, and the opposite of the reason the design changed. See Part 3 for the 800-vs-500 number itself. |
| 1.5 | Backlog: "**Live `rnsd` wiring** — only `RNS.Transport.register_announce_handler(...)` in a running Reticulum instance remains" (L302-304) | **FALSE (done, and hardened well past a first version)** | `ui/app.py:1091-1092` registers **two** live handlers — `_Handler` (all aspects → `registry.ingest_announce`) and `_HealthHandler` (`aspect_filter="rtnode.health"`). It runs on a daemon thread (`app.py:1117`) behind `monitor/mesh.py:83 attach_with_retry` (exponential backoff, 30 attempts) with two "already attached, not failed" signals (`mesh.py:55`) added after the 2026-07-30 deaf-listener incident. Beacon targets persist across restarts (`app.py:1129,1141`). A second listener exists at `workflows/report_proof.py:271`. |
| 1.6 | Backlog: "**Map mode UI** — placeholder (needs carried offline map tiles)" (L306) | **FALSE (a full offline map shipped)** | `ui/screens/scan_screen.py` is 1877 lines: MBTiles basemap, pinch-zoom, tap-to-inspect, mesh-link overlay, terrain shading, placement suggestions. Supporting modules `ui/map_tiles.py` (Web Mercator + SQLite MBTiles, `TILE_SIZE=512`), `ui/map_download.py`, `ui/map_projection.py`, `monitor/terrain.py` (SRTM line-of-sight). Tests: `tests/test_map_download.py`, `test_terrain.py`, `test_scan_map_perf.py`, `test_scan_lines.py`. (`assets/maps/` holds only `.gitkeep` — tiles are fetched on the medic, never committed.) |
| 1.7 | Backlog: "`nmcli` AP-join tested on a real Pi" listed as *not done* (L309) | **FALSE (done, hardware-verified)** | `workflows/rtnode_portal.py:170 join_ap_commands`, `:188 _default_join_ap` (10 attempts). `tests/test_rtnode_portal.py:252-253` pins the hardware finding: NetworkManager blocks `nmcli connect` for the login user via polkit and throttles plain rescans — hence the sudo + directed-rescan form. A whole field WiFi module now exists (`provisioning/wifi.py`, `ui/screens/wifi_screen.py`, `tests/test_wifi.py`). |
| 1.8 | The doc's own later note: "`announces_sending` (37) now keys off… `outgoing_announce_frequency > 0` (**falling back to the logfile**)" (L331-333) | **STALE — the fallback was reversed** | `diagnostics/network_mesh.py:53-59` falls back to `journalctl -u rnsd -n 500 --no-pager`, not `~/.reticulum/logfile`. The comment at `network_mesh.py:48-52` records why: "Verified on the live Pi: rnsd-under-systemd writes NO logfile (the path does not exist) and logs to the journal". The doc records the earlier, wrong conclusion. |
| 1.9 | `workflows/clone.py` described as "**Clone Tool** — copies OS/assets/monitoring-DB" (L50) | **STALE on both the name and the mechanism** | It is now **MITOSIS** (`workflows/clone.py:1`: "MITOSIS — replicate the medic onto a fresh Pi 5 (mode 6, formerly Clone Tool)"), **11** steps, and it **never copies an OS** — it rsyncs the tool tree, the 61 MB firmware cache, and installs from the carried wheelhouse with `--no-index`. "Generates a fresh identity" is **still true** (`generate_fresh_identity`; clone.py:5 "the source identity is deliberately never copied"). Live status a newcomer must know: `README.md:326` and `ui/screens/mitosis_screen.py:120` — **MITOSIS is not enabled**; the screen refuses rather than fake a clone. |

---

## Part 2 — Stale numbers and inventories

Not path-destroying, but every one of them is wrong, and the doc is the first
thing a newcomer reads.

| # | Claim (line) | Verdict | Reality |
|---|---|---|---|
| 2.1 | "**382 passing tests**" (L12, L20); later "Suite now **401 passing**" (L315); "test count only rises (11 → 401)" (L349) | **FALSE — off by ~8x** | **3284 passed, 11 skipped** (3295 collected). Measured on HEAD. The *rule* behind the number is still live: `SPEC.md:142` "The test count only goes up." Cross-doc note: `README.md:282,298` also still says **1776** — the README is stale too, just less so. |
| 2.2 | "`diagnostics/` … **7 modules, 91 checks**" (L43) | **STALE** | **8 modules, 95 distinct checks**: `reticulum_software` 21, `radio_firmware` 19, `system_health` 15, `rtnode_2400` 13, `client_connectivity` 10, `network_mesh` 7, `power_hardware` 7, `gnss` 3. `diagnostics/gnss.py` is new (commit `7b88ffc`, 2026-07-16 — three days *after* the doc's last edit) and no-ops unless the profile is a `WIRELESS_TRACKER` (`gnss.py:43`). Counting mechanism, for whoever updates this: there is no registry — checks are `_check(...)` calls (`diagnostics/base.py:96`) plus a few direct `Issue(...)` constructions; count distinct `check_name` literals. |
| 2.3 | "**Six Pi** modules (Power / Reticulum-software / Radio / System-health / Network-mesh / Client)" (L44-46) | **STALE** | **Seven.** `workflows/repair.py:38-46 MODULE_ORDER` adds `Gnss`. (`repair.py:3` still says "six" — the source is stale in the same way.) |
| 2.4 | "`build.py` (Pi, **10 steps**)" (L48) | **FALSE** | **17 steps** (`workflows/build.py:59-63` registry): `detect_hardware, confirm_radio_parameters, flash_rnode_firmware, set_firmware_radio_parameters, write_reticulum_config, install_software_stack, install_radio_rule, configure_services, install_health_reporter, install_status_server, apply_system_hardening, configure_bluetooth, set_hostname, final_verification, prove_the_node_reports, hand_the_usb_port_back, birth_certificate`. All 17 went green in a live acceptance birth on 2026-08-14 (`docs/WORKING_METHOD.md:462`). |
| 2.5 | "`rtnode_build.py` (Type-B, **5 steps** + GPS capture)" (L48-49) | **STALE** | **6 steps** (`workflows/rtnode_build.py:120,178`): `detect_board, flash_firmware, wifi_onboarding, verify_beacon, verify_sd_overflow, birth_certificate`. "+ GPS capture" survives — it lives inside `wifi_onboarding`. `verify_sd_overflow` (T-Beam Supreme store-and-forward) is new. |
| 2.6 | `monitor/registry.py`: "status + **6 h-staleness→red**" (L54) | **FALSE — and the 6 means something else now** | `monitor/registry.py:26` `STALE_ALERT_HOURS = theme.NOT_HEARD_ALERT_HOURS # 18`, applied at `registry.py:314`. `ui/theme.py:39` — 18 h is "3x the **6 h beacon cadence** — tolerate 2 missed announces before alerting". So 6 h is now the *cadence*, not the threshold. There is also a quiet tier (`theme.py:41 QUIET_AFTER_HOURS = 12`) and an outage-escalation grace the doc has no concept of (`monitor/node_watch.py:31,37`: 72 h solar/battery, 24 h mains). Everything else in the registry sentence still holds (dst-hash keying, `ingest_announce` at `registry.py:557`, JSON persistence, commissioning log `:920`, `navigation()` `:212`). |
| 2.7 | `ui/` inventory: "screens (`monitor`, `repair`, `node_detail`, `build`)", widgets "`hex_status`, `stat_bar`, `sidebar`" (L58-60) | **STALE — undercounts screens ~7x, widgets ~8x, and 3 of the 4 named screens were renamed** | `ui/screens/` holds **27 screens** (28 files incl. `__init__.py`); only `node_detail_screen.py` still carries a name from the doc. `monitor`→`vitals_screen.py`, `build`→`birth_screen.py`, `repair`→ split into `triage_screen.py` + `probe_screen.py`. `ui/widgets/` holds **24** (25 files incl. `__init__.py`); the three named ones survive. Entire packages the doc has no entry for: `provisioning/` (36 modules plus a `security/` apply-and-rollback kit and `sudoers.d/`), ~35 top-level `ui/*.py` (`i18n.py`, `fonts.py`, `cert_store.py`, `vault_unlock.py`, `map_*.py`, …). |
| 2.8 | "**UI deps (medic only):** `kivy`, plus `segno`… the tested core never imports them, so CI stays third-party-free" (L30-34) | **STALE** | segno is still the QR lib (`ui/qr.py:94-97`, lazy import, `None` when absent). But `assets/requirements.txt` now pins **five** runtime packages: `rns==1.3.8`, `lxmf==1.0.1`, `segno==1.6.6`, `kivy==2.3.1`, `adafruit-nrfutil==0.5.3.post16` — the last is **not** UI-only (rnodeconf shells out to it for every nRF52 board). And CI is no longer third-party-free: `.github/workflows/ci.yml` installs `PyYAML` and `Pillow` as **test-only** deps (`tests/test_cloud_init_seed.py:17`, `tests/test_terrain.py:15`), added because CI failed at import for every commit after `88fabf0` while passing locally. |
| 2.9 | "Two golden vectors are pinned" / "**14 bytes**, big-endian" (L71-74) | **STALE (undercount, not wrong)** | The beacon is now **versioned**: v1 = 14 bytes, **v2 = 20 bytes** (`monitor/health_beacon.py:49-50 PAYLOAD_LEN=14 / PAYLOAD_LEN_V2=20`), the v2 tail carrying battery mV, battery %, power flags, LoRa SNR/RSSI (`firmware/rtnode-2400/README.md:32-47`). **Three** golden vectors now (`tests/test_health_beacon.py:35,44,248`), plus a cross-language contract test that compiles `HealthBeaconPack.h` with g++ and asserts byte-equality with the Python encoder (`tests/test_firmware_beacon_contract.py`). The decoder tolerates trailing bytes so a v3 can append without a lockstep release. |
| 2.10 | "`assets/` — 4 Reticulum config templates; `scripts/flash_rtnode2400.sh` + `apply_neopixel_patch.py`" (L61-62) | **STILL TRUE, now an undercount** | Exactly 4 templates in `assets/configs/`; both named scripts exist. But `assets/scripts/` now holds 8 files and `assets/` has 9 subdirectories the doc never mentions (`apps, boards, firmware, fonts, i18n, maps, packages, sketches, ui`) plus `requirements.txt`. |
| 2.11 | "**Hardware milestone**: a physical Heltec V4 named **TRUTH** was flashed this session" (L103-108) | **STALE as a milestone** (true as history) | TRUTH survives only as a fixture name in `tests/test_registry.py`. The live fleet is FAITH and others (`monitor/mesh.py`, `ui/app.py`, `monitor/http_status.py`). The current hardware high-water mark is the SKYFINGER acceptance birth of 2026-08-14 — all 17 build steps green, certificate written, health beacon on VITALS a minute later (`docs/WORKING_METHOD.md:456-466`). |
| 2.12 | Parser tables 1-4 (L232-292): the exact strings/regexes each check looks for | **STALE for most rows** | Table 1 (`rnstatus` text): all three rows dead — `radio_interface_up` is `iface["status"] is True` from `rnstatus --json` (`reticulum_software.py:82`); `path_table_populated` is `len(paths) > 0` from `rnpath -t --json` (`network_mesh.py:67`); `channel_congestion` reads `channel_load_short` (`network_mesh.py:76`). The strings "paths known" and "Channel load" no longer appear anywhere in the repo. Table 2 (`rnodeconf --info`): labels are now `\s*:` regexes with value comparison, `firmware_hash_set` **no longer exists** (replaced by `firmware_hash_valid`, `radio_firmware.py:171-180`, which pushes `assets/scripts/fw_hash_probe.py` because `--info` does not reveal it), `LATEST_FIRMWARE` is **`1.86`** not 1.80 (`radio_firmware.py:25`), `spreading_factor` compares numerically against the profile rather than matching `"…: 9"`. Table 3 (`rnpath -t` non-empty text): now JSON. Table 4 (`chronyc tracking`): **STILL TRUE verbatim** (`system_health.py:42`) with the timedatectl fallback added. |
| 2.13 | "`channel_congestion`: **⚠ OPEN — confirm the scale**… my read of RNS is it's a **fraction**" (L207-211) | **FALSE — resolved, and the doc's guess was the wrong one** | It is a **percent (0-100)**. `network_mesh.py:71-80`: threshold `load < 70.0`, with the comment "verified on a live node: human rnstatus prints 'Ch. Load : 0.14%' while the JSON value is 0.14; a busy node read 18.66 … the old `load < 0.70` + `load*100` read a healthy node as '675%'". Two regression tests pin it (`tests/test_diagnostic_network_mesh.py:96-106`). |
| 2.14 | "**ARCHITECTURAL** — redesign radio_firmware to read `rnstatus --json` on a live node" (L170-182) | **PARTLY done — the diagnosis was right, the prescribed redesign was not the fix taken** | `diagnostics/radio_firmware.py:71,84` still shells out to `rnodeconf {port} --info`. What was added instead is a **live-mode gate** (`radio_firmware.py:125-134`): if the info block is empty *and* `fuser` shows the port held *and* `rnsd` is active *and* an RNodeInterface exists, the module early-returns a single informational `radio_in_service` issue and runs nothing else — so the false-positive-dead-RNode failure mode the doc warned about is closed. `has_info` gating is central and defensive (`radio_firmware.py:111`, `_device_read` `:51-57` — "NOT `bool(info)`: error text lies", because rnodeconf exits 0 on "Could not open port"). One check did move to JSON: `antenna_rssi` prefers `RNodeInterface.noise_floor` (`radio_firmware.py:297-301`). |
| 2.15 | Backlog: "commissioning-log UI polish"; "bundle an emoji font (currently short text labels instead)" (L309) | **STALE / reversed** | The commissioning log shipped (`ui/screens/node_detail_screen.py:251`, backed by `registry.py:920`, `tests/test_commissioning.py`). The emoji font is now a **rejected decision, not a backlog item**: `TODO.md:64` "No emoji / glyph fonts on the Pi — ship icons as PNGs, text stays ASCII", enforced by `tests/test_triage.py:88 test_guidance_is_emoji_free_for_the_field_pi`. Icons are drawn vector primitives and PNGs, not "short text labels". |

---

## Part 3 — The firmware contracts (the doc's strongest section)

All four contracts in "Cross-project contracts with `5ugAv/RTNode-2400` — LOCKED"
(L68-86) survive. Only the location one has drifted.

| Contract | Verdict | Evidence |
|---|---|---|
| 1. Health beacon on aspect `rtnode.health`, `app_data` of an RNS announce, big-endian | **STILL TRUE** | `monitor/health_beacon.py:6`, `monitor/health_poll.py:5`, `firmware/rtnode-2400/README.md:29-30`. Live handler filters on that exact aspect (`ui/app.py:1059`). See 2.9 for the byte count. |
| 2. On-demand poll, 1-byte opcode `0x01`, unknown opcodes are no-ops | **STILL TRUE** | `monitor/health_poll.py:29 OPCODE_FULL_HEALTH = 0x01`, `:32-33 build_request`. Firmware side: `firmware/rtnode-2400/README.md:12` ("answers the medic's on-demand `0x01` poll"). Verified live 3/3 in ~2 s per the project memory. |
| 3. Captive portal: `POST /save` form-urlencoded at `http://10.0.0.1`, AP `RTNode-Setup` open; `freq` MHz decimal string, `bw` Hz int, `sf`/`cr`/`txp`, `ssid`/`psk`/`node_name` | **STILL TRUE, now an undercount** | `workflows/rtnode_portal.py:29,31,36,98-102` — every named field and unit matches exactly. The full contract is 30 fields (`docs/RTNODE2400_INTEGRATION.md:15`), including the `advert_*`, `mdns_*`, `tcp_*`, `ifac_*`, `disp_*` groups the handover does not list. |
| 4. Location / Section E | **MECHANISM STALE (see 1.4) + a live numeric disagreement (below)** | — |

### The 800-vs-500 disagreement — which files disagree, and which is right

| File | Says | Basis given in the file |
|---|---|---|
| `docs/HANDOVER.md:83` | firmware fuzz **~800 m** | firmware-side agreement |
| `docs/RTNODE2400_INTEGRATION.md:98` | jitter = deterministic offset up to **~800 m**, constant named **`ADV_JITTER_RADIUS_METERS`**, "fixed at 800 m by design" | firmware-authored contract answers |
| `monitor/geo.py:293` | `FIRMWARE_JITTER_M = **500.0**` | "VERIFIED against the upstream source this firmware is forked from, jrl290/RTNode-HeltecV4 (read 2026-08-04): …approximately half a kilometre" |
| `README.md:237-239` | firmware applies "its own deterministic **~500 m** offset", total "**~1.3 km**" | derives from `geo.py` |
| `workflows/rtnode_build.py:431-435` | medic fuzz ~800 m; total "**~1.3 km**" | derives from `geo.py` |
| `tests/test_geo.py:192-194` | asserts `public_pin_radius_m(True) == FUZZ + FIRMWARE` and `> 1000` | pins whatever `geo.py` says |

**Note first that the two 800s are not the same 800.** `monitor/geo.py:276
FUZZ_RADIUS_M = 800.0` is the **medic's own** fuzz — a different, newer layer
(see 1.4) that the handover predates entirely. The disagreement is only about
the **firmware's** offset: 800 (both docs) vs 500 (`geo.py` and everything
downstream of it).

**Which is right: not resolvable from this repo.** `firmware/rtnode-2400/`
vendors only the four health-beacon headers (`BirthCry.h`,
`HealthBeaconPack.h`, `HealthStatus.h`, `HealthBeacon.h`) — no advertisement
code, so `ADV_JITTER_RADIUS_METERS` cannot be read here. `firmware/rtnode-2400/README.md:16-17`
says the buildable fork lives on the medic at `~/RTNode-2400/`.

**Weight of evidence favours 800**, and the disagreement errs in the unsafe
direction: `RTNODE2400_INTEGRATION.md` names the actual firmware constant and
its value, whereas `geo.py` quotes *prose* ("approximately half a kilometre")
from the **upstream project the fork derives from** — the fork may well have
changed it. If 800 is right, the true public-pin radius is **~1.6 km** and
every "~1.3 km" in the tree understates the displacement by 300 m. `geo.py:296-305`
argues in its own docstring that understating this is the failure that matters.

**One command settles it**, on the medic, not from here:
`grep -rn ADV_JITTER_RADIUS_METERS ~/RTNode-2400/`.

**Live context the rewriter needs:** commit `0599459` (2026-08-15, docs only —
`docs/NEXT_BRIEFS.md`) records an operator decision to **stop advertising the
fuzz** on screen at all ("a protection that cannot be relied on must not be
described"), replacing it with an operator-chosen offset and flagging that
fuzzing a deliberate decoy point could land a pin on a stranger's home. The
machinery stays; the claims go. So Section E is not merely stale — it is
mid-revision.

---

## Part 4 — Firmware-side open issues (L88-101)

| Issue | Verdict |
|---|---|
| Heap leak under persistent TCP | **STILL OPEN** — no resolution recorded anywhere in the tree. `diagnostics/rtnode_2400.py:14-19` still carries it as "known-issue awareness (fork status)". Tool surfacing unchanged: `heap_fault` (`rtnode_2400.py:87`), `heap_low` (`:92`). The 40 KB / 3-strike / ~90 s semantics are corroborated firmware-side at `docs/RTNODE2400_INTEGRATION.md:77`. |
| WiFi lockup under weak signal | **STILL OPEN** — same docstring; `wifi_link` (`rtnode_2400.py:101`) and `wifi_rssi` with the doc's −75/−85 thresholds (`:106-114`). `git log --grep=lockup` returns only the commit that added HANDOVER.md itself. |
| Hardware watchdog armed confirmation | **STILL OPEN / UNVERIFIABLE here** — `watchdog_armed` still reads beacon bit b4 (`rtnode_2400.py:97`, `health_beacon.py:252,321` — `0x10`, matching the documented bit). No firmware confirmation is recorded in-repo, so the doc's "treat as informational" advice stands. |

The doc's framing of these as "firmware-side, not tool bugs, but they shape
what the tool should watch for" is still exactly right, and `rtnode_2400.py`'s
own docstring is the living copy of it.

---

## Part 5 — Still true (worth keeping in any rewrite)

| Claim | Evidence |
|---|---|
| Headless-testable by design; every I/O seam injected (GPS reader, HTTP POST, AP-join, SSH runner); `EmulatedConnection` with rule list / `^`-prefix / first-match-wins | `transport/connection.py:464`; injected runners throughout `monitor/`, `diagnostics/`, `workflows/`. |
| `transport/connection.py` inventory — `Connection`, `SSHConnection` (retries transient 255s), `SerialConnection` (sentinel framing via `rfind`, base64 push), `EmulatedConnection`, `auto_detect_connection` | `transport/connection.py:21, 85, 394, 464, 524`. One addition the doc lacks: `LocalConnection` (`:320`). |
| `rtnode_2400.py` is beacon-driven; Type-B has no text console | `diagnostics/rtnode_2400.py:1-19`, `_BEACON_RE` at `:32`, decode at `:52`. |
| CI runs on 3.11 / 3.12 | `.github/workflows/ci.yml:12-13`. |
| Python 3.14.6 / pytest 9.1.1 / kivy 2.3.1 | Reproduced on this machine. Caveat: these are dev-box facts, and `assets/requirements.txt` pins for **Python 3.13 / aarch64** (the medic), `workflows/wheelhouse.py:12` records cp313/aarch64. |
| MIT licence; repo `github.com/5ugAv/Reticulum-Node-Medic` | `LICENSE:1-3` ("Copyright (c) 2026 Suga (5ugAv)"); `git remote -v`. |
| "All five operating modes represented" | Still five: VITALS / SCAN / BIRTH / TRIAGE / PROBE (`README.md:26-46`), with MITOSIS a sixth that has a screen but is not enabled (`README.md:58,326`). |
| Board-id byte == RNode `BOARD_MODEL`, 0x3F = Heltec V4, tool mirrors the full enum | `monitor/health_beacon.py:85,101` (`0x3F: "Heltec32 V4"`, "the tool and firmware share one enum"); the board catalogue in `workflows/rnode_boards.py` now covers **15** boards, well beyond the V4 the doc knew. |
| Fault bit b6 = internal free heap < 40 KB sustained ~90 s (3 strikes) | `docs/RTNODE2400_INTEGRATION.md:77`. |
| Same diagnostic code in all three self-healing tiers, only the `Connection` differs | `diagnostics/base.py:9`, `workflows/repair.py:6`. |
| `_priv()` sudo escalation in the diagnostics base | `diagnostics/base.py:156-160` (`sudo -n`). **Caveat for a newcomer:** this is for *nodes*. The **medic's own** sudo is now scoped (`provisioning/sudoers.d/nodemedic` — "REPLACES the blanket `nodemedic ALL=(ALL) NOPASSWD:ALL`"), so a privileged action on the medic that is not in that file fails, by design. |
| The three "FIXED since" items (L160-168): `clock_drift` timesyncd fallback, `serial_acl` unverifiable-when-getfacl-absent, `detect_rnode_port()` via `/dev/serial/by-id/` | `system_health.py:41-56`; `reticulum_software.py:135-136,227`; `workflows/build.py:97-101`. All present, all since tightened. |
| Strict TDD; test count only rises; commits are logical batches | `SPEC.md:142`; the number in the doc is wrong (2.1) but the rule is live. |
| "Type-B can't do LXMF (embedded C++ RNS is core-only)" | No contradiction found in-repo. Nuance worth carrying: upstream *does* seal its discovery announce with an LXMF proof-of-work stamp (cost 14) — `workflows/rtnode_portal.py:116-121`. That is a stamp, not LXMF messaging, but the flat "can't do LXMF" reads too absolutely now. |

---

## Part 6 — UNVERIFIABLE from this worktree

| Claim | Why |
|---|---|
| "public · CI green" (L7) | No network access in this audit. The remote and licence check out; visibility and current CI status do not. |
| "RTNode-2400 identity persists in **LittleFS**, rotates only on full chip erase" (L112-114) | Firmware-side property; the vendored subset carries no NVS/LittleFS code. |
| "Cross-impl hash match verified on hardware" (L74) | Historical hardware event. What *is* verifiable is stronger and mechanised: `tests/test_firmware_beacon_contract.py` compiles the firmware header with g++ each run and asserts byte-equality. |
| "Placeholder repo deletion (firmware side) — needs a `delete_repo` token" (L311) | Action in a different repo. `delete_repo` appears nowhere outside `HANDOVER.md:311`. |
| Offline PlatformIO cache (L307-308) — listed as not done | **Believed STILL TRUE (not done):** nothing carries `~/.platformio`. Two *different* caches were built and may be mistaken for it: `workflows/updater.py:31` (`~/.config/rnodeconf/update`, offline RNode firmware) and `workflows/wheelhouse.py:26` (Python wheels → `assets/packages`, "17 wheels (30 MB)" verified on the medic). Marked here rather than in Part 5 because "the firmware side to provide a pinned version manifest" cannot be checked from here. |

---

## Part 7 — Two latent code bugs this audit surfaced

Not staleness in the doc — real defects found while checking it. Recorded here
so they are not lost.

1. **`mesh_ping_l2` still calls `rnping`**, a command the handover established
   does not exist in RNS 1.3.7 (`diagnostics/network_mesh.py:90`). The
   destination half of the bug was fixed; the command name was not. The
   emulator rules assert the literal `rnping`
   (`tests/test_diagnostic_network_mesh.py:44,181`), so the suite is green
   either way — the exact "passes in emulation, worse than no check on real
   hardware" pattern the handover names.

2. **`warm_boot_param_mismatch` (50) reads a file two other sites in the same
   package assert does not exist.** `diagnostics/reticulum_software.py:119`
   does `tail -n 300 ~/.reticulum/logfile` and greps `"mismatch"`, while
   `network_mesh.py:48-52` and `base.py:194-196` both record the live-Pi
   finding that systemd-run `rnsd` writes **no** logfile and logs to the
   journal. On a real node this check reads an empty string and always passes.
   `base.py:198-201` already greps the journal for the real strings ("Radio
   state mismatch", "Aborting RNode startup") — the fix is to route check 50
   through it.

---

*Audit method: every factual assertion in `docs/HANDOVER.md` was checked
against HEAD `5d9c24a` — code, tests, and git history. Counts were measured by
running the tool, not estimated. Where a claim could not be checked from this
worktree it is marked UNVERIFIABLE rather than guessed.*
