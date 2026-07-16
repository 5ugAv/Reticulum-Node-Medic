# Node Medic — roadmap checkpoint (2026-07-16)

State: the medic is a **boot-to-working appliance** — power on → radio via the
serial splitter (LoRa + GPS on one cable) → touchscreen boots into the UI.
All six modes live (VITALS / SCAN / BIRTH / TRIAGE / PROBE / MITOSIS), offline
basemap with pan/pinch + per-node street detail, live node diagnosis proven
(FAITH: alert = weak WiFi, −90 dBm). 819 tests passing.

## 1. Next up — code, no blockers

- [ ] **Provenance tiers on VITALS + SCAN** (#54) — the big one:
      dedupe mesh destination hashes per device (one device = one row);
      Kin / Kindred / Neighbour multi-select tabs; hexagon fill-weight
      (kin = solid, kindred = thick outline, neighbour = thin outline);
      others' locations render as fuzzy circles (privacy model, engine done).
- [ ] **VITALS tap-through → node detail** (#55): name on every row; tap →
      detail page (notes, location + nav links, provenance, WHY-it's-red text,
      history graph + flags) with **Probe this node / Triage here** actions.
- [ ] **TRIAGE live verify** (#42): send an announce at the screen — confirm
      the triangle moves on real RSSI/SNR; wire Save/lock → session record.
- [ ] **First-link wizard UI in BIRTH** (#52): engine done — home base, walk
      out ≤10 km, step 1 km closer until strong; result seeds observed reach.
- [ ] **Guided/expert mode** (#45): needs two user calls — where the toggle
      lives, what expert reveals per screen.

## 2. Waiting on hardware

- [ ] **GPS antenna arrives** → first fix (open sky) → birth-cert locations →
      node dots + street-detail circles activate → Triage GPS stamps →
      interference log wiring (#47).
- [ ] **SD card arrives** → real MITOSIS clone onto the twin Pi 5, and #53
      (clone runs `scripts/setup_boot.sh`; genericise the RNode by-id port).

## 3. Waiting on the user

- [ ] **Publish the Tracker installer repo** (#41) — flips
      5ugAv/Heltec-Wireless-Tracker-RNode public; also publishes the shared
      signing key (intended). Explicit go-ahead required.

## 4. The designer (Sophie) pass — screens land here

Assets first: 6 mode icons (🫀🧫🥚🩺🩻🧬 as PNGs — no emoji font on the Pi),
app logo, boot splash, palette (drop-in via `ui/theme.py`).
Then the screens whose engines are already built + tested:

- [ ] Portrait nav — sidebar → bottom bar, orientation support (#50)
- [ ] History graph + pattern-flag chips on node detail (#43)
- [ ] SCAN graph view (ring layout), lines on/off toggle, tap node/edge (#46)
- [ ] Suggestion pins + "Test this location" → Triage handoff (#48)
- [ ] Interference map markers + toggle (#47)
- [ ] Regenerate the screen pack for Sophie with the new mode names

## 5. Long-view (designed, not started)

- [ ] Kindred detection via health-beacon heard / community birthmark signing;
      medic registry gossip over LXMF
- [ ] "Join nearby networks" bridge suggestions (foreign announces = evidence)
- [ ] Battery runtime line — dormant until a node type reports battery (#49)
- [ ] Street-level bulk detail via a bulk-entitled tile source (keyed provider
      or Protomaps), if per-node circles aren't enough

## Standing constraints (learned the hard way)

- No emoji / glyph fonts on the Pi — ship icons as PNGs, text stays ASCII.
- Never bulk-fetch tile.openstreetmap.org — Carto CDN + attribution + the
  same-tile circuit breaker are load-bearing.
- Calibrate against *this* mesh, never a datasheet: Triage score, noise haze,
  placement reach, first-link distance are all measured, not assumed.
- Exact node locations never leave the builder's medic; only the
  deterministic fuzzed pin is ever shared.
