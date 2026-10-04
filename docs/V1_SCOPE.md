# Node Medic v1 — what ships, and what waits

**Frozen 2026-09-04.** Everything below is the operator's call, written down so
that "is this v1?" has an answer that does not need re-litigating.

---

## The point of v1

**One medic, cloned, handed to a builder who did not make it.** A real person,
who will use it to birth nodes and watch them.

That is the whole release criterion. Not a feature count — a person using it
without the person who built it sitting next to them.

## The three versions

| | Who it is for | What it adds |
|---|---|---|
| **v1** | A builder with access to shops and mains power | The tool as it stands, made flawless |
| **v1.2** | The same, plus salvage | Second-hand radios, recycled hardware, the "show me what you got" path |
| **v2** | 100% remote communities | The full brief: no shops, no internet, whatever is lying around |

v2 keeps the goalposts. v1 does **not** pretend to be there yet: hardware is available, so v1 picks the BEST hardware rather than the
most findable. That is not a retreat from the ethos — it is the honest version
of where the tool actually is.

## What must be flawless before the clone is handed over

Five things. Nothing else blocks the handover, and no more get added.

- [x] **Birth** — a node built end to end, on the boards already working
- [x] **Monitoring** — nodes appear, and their health comes back over the mesh
- [ ] **Maps** — placement and the node map
- [ ] **Antenna guide** — the choosing and testing path
- [x] **Chat / communication** — messages actually move
  _Ticked 2026-10-01._ First live exchange, over LoRa: the operator's phone (Columba, Heltec MeshPocket over Bluetooth) announced as `5a150015…` and the medic listed it by name under Heard on the mesh; medic → phone "your node medic pal" 23:28:28 `delivered` (double tick on the phone); phone → medic "It works!" 23:29:27 on the medic's own screen and in its store (`~/.reticulum-node-medic/chat/messages.json`). 1 hop via the RNode. Both directions read back from disk, not from the photo.

### What the two ticks rest on (checked 2026-09-27, not remembered)

Ticked against the live medic, because a list nobody has verified is worse
than no list — this one had been carrying three problems that were already
solved, and planning off it wasted a session.

* **Birth.** ELSEWHERE and skyfinger both exist as Pi propagation nodes and
  both answer. skyfinger was built after 2026-09-11, i.e. through the flow as
  it now stands.
* **Monitoring.** Health beacons arriving OVER THE AIR on cadence —
  `announce 5a1f001f len=20 beacon=yes` at 07:24 and `5a170017 … beacon=yes`
  at 03:30 on 2026-09-27 — ingested, and folded to one row per device
  (14 raw records -> 4 VITALS rows; ELSEWHERE's five destinations are one
  row). The LoRa interface shows ↓424 KB received while the LAN AutoInterface
  sits at **0 peers, 0 bytes**, so none of it came over the network.
* The medic's own ear was proven in both directions on 2026-09-11 — see
  the memory note `ear-test-passed-both-ways`.

The remaining three need a person at the bench, not more code: a clean walk
of the antenna path, a two-way message that actually moves, and the map's
placement flow end to end.

Plus the one comprehension blocker, which is artwork and has a lead time:

- [x] **A front page a stranger can read.** BIRTH / TRIAGE / MITOSIS is a
      private language. This is the single largest barrier to anyone but the
      builder using the tool, and it cannot be fixed in code — the words are
      painted into `assets/ui/front_page.png`.
  _Ticked 2026-09-29._ The poster shipped through thirteen revisions with two reviewers (final verdict: "finished — stop"); the five cards read VITALS / MAPS / BUILD / ANTENNA / CHAT with one-line captions; the tap-map's card labels and both emblem zones are pinned against the painted pixels in tests/test_front_page_vocabulary.py; the setup tour opens every screen with the painted word (test_tour_titles_open_with_the_painted_word). Verified on the glass by capture, not by reading the code.

## The hardware question v1 has to answer

Which Raspberry Pi pairs with which radio board, and **which pairing is the
most energy efficient**, so a solar node needs the smallest panel and battery.

What exists today (`workflows/power_compat.py`) answers a DIFFERENT question:
worst-case burst draw versus the Pi's USB budget — "will this brown out". That
is a safety check, not an energy budget. Almost every figure in it is marked
`"src": "estimate"`.

Nothing in the repo records average draw, and average draw is what sizes a
solar installation. This is a measurement job, not a research job.

## Banked — built, working, NOT promised in v1

Present in the code, tested, costing nothing to leave alone. Do not polish
these before the handover.

- Records encryption and the Settings switch
- "Show me what you got" salvage screen and the recycling how-tos
- The handheld-radio path and the carried Dire Wolf
- Languages beyond the recipient's own

## The rule that keeps this frozen

**Until medic #2 is in someone's hands, new ideas get WRITTEN DOWN, not built.**

This applies to the assistant as much as the operator: no more "want me to
build X next?" at the end of a turn. Ideas go to the list below.

## Ideas parked (not v1)

- **Our own RNode firmware releases as BIRTH's source (operator, 2026-10-03).**
  Today the official OLED boards (V3, LoRa32, T3-S3, T-Beam) are flashed by
  `rnodeconf --autoinstall` from Mark's cached upstream release, so they get
  the stock face and none of the fork's work; only the V4 is compiled from
  `5ugAv/RNode_Firmware` (`medic-cross`). The clean version: build every
  target from the fork (`make release-all` + `release-hashes` already exist),
  publish them as GitHub Releases on the fork, and point
  `workflows/updater.py`'s FIRMWARE_URL at the fork — autoinstall and the
  offline cache stay exactly as they are, only the source changes. Needs the
  release hash files rnodeconf verifies, and one proof per board on glass.
  Note: a host-attached RNode does not itself beacon health — the Pi reporter
  does — so the gain here is the face, the NeoPixel and Tracker work, and one
  source of truth, not the beacon.

- **More to show while the antenna test reads (operator question, 2026-10-04).**
  The screen shows the dBm and nothing else. What the same polls already carry,
  best first: the **running median** (the figure that actually ranks, shown as
  it settles instead of only at the end); **steadiness** (the spread across the
  samples — a wobbling reading means the antenna or the keeper's hand is moving,
  not that the antenna is bad); the **difference to the best antenna so far**
  this session ("+3 dB on Antenna A"); the **interference flag**
  (`EarReading.interference_dbm`, collected today, never shown mid-read); and a
  small **sparkline** of the samples. Deliberately left out: airtime and channel
  load (they describe the neighbours, not the antenna) and reply rate (the
  board answers about one poll in three by design, so it would read as a fault).

- **The medic fetches everything itself (operator, 2026-10-04).** Field
  readiness tops up RNode firmware, the phone apps and (since 2026-10-04) the
  Python wheels while on Wi-Fi; the offline map is fetched from MAPS, the Pi OS
  image is copied on by hand (`~/pi_os_lite.img.xz`), and the build toolchain
  arrives with the first online firmware build. The full version: a Pi OS
  image downloader (official URL + published sha256, ~450 MB, behind a
  size-and-consent gate), an explicit toolchain pre-install step (arduino-cli
  esp32 core + the PlatformIO platform), an offline-map "around my nodes"
  auto-area (the roster's positions plus the medic's own fix), and the apt
  .deb cache (cage, direwolf) in the readiness audit so a clone can finish.

- **A distance-aware "too close" hint on Ping (2026-10-04).** The two-hop
  "a radio within a couple of metres is too loud to decode" line is a guess;
  with the medic's GPS fix and the node's recorded position it can be said
  only when they really are within a few metres, and dropped otherwise.

- Text to speech — the medic has no speaker; revisit if hardware changes
- Arabic and right-to-left layout — needs RTL work first
- More boards past the current 16
- Phone companion app
- Front-page mode renaming beyond the artwork itself
