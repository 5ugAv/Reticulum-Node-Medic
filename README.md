# Reticulum-Node-Medic

**Easy to use Reticulum network building/monitoring/maintenance tool.**

For [Reticulum](https://reticulum.network) meshes. It runs on a Raspberry Pi 5
with a 5-inch touchscreen, powered from a battery
bank, and reaches nodes over USB serial, SSH, a USB-gadget cable link, or by
running commands on the medic itself. Built for a community LoRa mesh on Heltec
WiFi LoRa32 boards and Raspberry Pi nodes. It is designed to work with no
internet in the field: firmware, Python wheels, Reticulum config templates and
board images are all carried on disk.

Two things must be staged while online, and the tool says so rather than
pretending otherwise: **map tiles** (downloaded per-region, see [Map and
placement](#map-and-placement)) and the **Raspberry Pi OS image** used for SD
imaging.

> This is the **tool**. It is kept separate from the node firmware (RNode / the
> RTNode-2400 5ugAv fork) that it inspects and repairs. Only the health-beacon
> wire format is vendored here, so the two cannot drift — see
> [`firmware/`](firmware/).

## Operating modes

The front page is an image with a tap map ([`ui/home_zones.py`](ui/home_zones.py)).
It offers **five** modes:

1. **VITALS** 🫀 — the node dashboard. A filterable list (All / OK / Warn /
   Alert) of every node the medic knows about, with hex status indicators,
   capability chips and a "quiet — not heard in *N* h" divider. Fed by a live
   Reticulum announce listener and a 30-second poll cycle. It will not invent a
   reading: a stat is drawn only when it was actually measured. Tapping a node
   opens its detail page.
2. **SCAN** 🧫 — a geographic map of located nodes over offline raster tiles,
   with pan/pinch, a mesh-lines overlay, the medic's own GPS fix, and
   "Use this position" to hand a location straight to BIRTH.
3. **BIRTH** 🥚 — provisions a node from bare hardware. Entering BIRTH starts a
   step-by-step guided walkthrough that detects what is plugged in and decides
   whether the board should be **born** (flashed and provisioned) or **adopted**
   (enrolled as kin without reflashing).
4. **TRIAGE** 🩺 — antenna aiming and site assessment. A thermal bullseye scores
   RSSI / SNR / noise / headroom live while you move the antenna, with a
   screen-edge glow that brightens toward the best spot found. A "NOT READING"
   cover drops over the score after 2 seconds of silence so a stale reading
   cannot be mistaken for a live one.
5. **PROBE** 🩻 — diagnoses and repairs. One button runs the full diagnostic with
   streaming per-category progress, then offers "Fix all" or individual fixes.
   A fix is only reported as fixed after the original check is re-run and
   passes. A second button opens **Self Diagnose**, which points the same idea
   at the medic itself.

Behind those five, the app registers **26 screens** in total
([`ui/app.py`](ui/app.py)) — node detail, certificate view, settings and its
sub-pages (WiFi, language, storage, date/time, radio defaults, trusted
operators, tool identity), notifications, comms, the SD-card imager, and the
credits page hidden under the red cross.

**MITOSIS** 🧬 — cloning the medic onto a fresh Pi — has a screen and a
workflow, but the target-Pi flow is **not wired**. On the medic it refuses with
"still under construction" rather than faking a clone.

## Node types

- **Type A — Raspberry Pi transport node** (`rnsd` + `lxmd`, attached RNode).
- **Type B — Standalone RTNode-2400** (5ugAv microReticulum fork; no Pi; health
  via RNS announce beacons — see below).
- **Type C — RNode only** (a LoRa32 board as a Pi node's radio interface).

`node_profile.py` models five node roles: transport, LXMF propagation, gateway,
Meshtastic bridge, and unknown.

### Board catalogue

[`workflows/rnode_boards.py`](workflows/rnode_boards.py) carries **15 boards** —
14 flashed through `rnodeconf --autoinstall` (LilyGO LoRa32 v1.0/v2.0/v2.1,
T-Beam, T-Beam Supreme, T3S3, T-Deck, T-Echo, Heltec LoRa32 v2/v3/v4, Heltec
Mesh Node T114, RAK4631, Seeed XIAO ESP32S3) plus the Heltec Wireless Tracker,
which is not in official RNode firmware and is built from a patched tree.

**9 of the 15** have a verified autoinstall band map. The other five official
boards refuse to flash rather than guess at the answers their device menu
expects. **10 of the 15** have a photograph in `assets/boards/`, shown to the
operator so an identification made from silicon they cannot see has a visual
check. Only Heltec V3 and V4 are hardware-verified end to end.

Three boards can be built as a standalone RTNode-2400
([`workflows/rtnode_build.py`](workflows/rtnode_build.py)): Heltec V3, Heltec V4
and T-Beam Supreme.

## Australian deployment defaults (all overridable)

| Frequency | Bandwidth | SF | Coding rate | TX power |
|---|---|---|---|---|
| 915.125 MHz | 125 kHz | SF9 | 4/5 (CR5) | 17 dBm |

Regulatory basis: Australian LIPD Class Licence, 915 MHz band.

These are a mesh-wide invariant: nodes can only hear nodes on the same
settings. Every node is built with them baked in. If the tool's defaults are
changed, the home screen shows a persistent badge until they are reverted, with
a one-tap revert that also retunes the medic's own radio.

## Diagnostics

**94 checks** across 8 modules, each with a plain-English description, a
severity (`critical` / `warning` / `info`) and, where possible, an auto-fix.
Checks never short-circuit — one failure cannot hide a later one.

The Pi repair chain runs 7 modules in operator-visible order (83 checks):

Power & hardware → Reticulum software → Radio & firmware → System health →
Network & mesh → Client connectivity → GNSS.

The eighth module covers standalone RTNode-2400 boards (11 checks). The **same**
Pi diagnostic code runs in all three self-healing tiers — on-node systemd timer,
remote over SSH, or physical serial — only the `Connection` differs.

The three-level ping lives here as checks, not as a separate screen: L1 serial
loopback (`radio_loopback`), L2 mesh ping via `rnping` (`mesh_ping_l2`), L3
announce heard by the tool (`announce_heard_l3`).

The RTNode-2400 module is **beacon-driven**: those boards have no text console,
so on a physical visit the tool captures the passive serial `[HealthBeacon]`
line, decodes it with the shared codec and derives its checks from the decoded
fields (plus a boot-log FATAL scan) — the same wire contract used over the mesh.

### Self Diagnose (the medic itself)

**11 checks** on the tool's own health
([`monitor/self_diagnose_runtime.py`](monitor/self_diagnose_runtime.py)): USB
devices present, serial splitter, GPS telemetry freshness, disk space, `rnsd`
service, CPU temperature, power throttling, WiFi signal, clock sync, Reticulum
responding (`rnstatus`), and `lxmd` (only expected when the medic is in home
propagation mode). Three have one-tap automatic repairs — restart the splitter,
`rnsd`, or `lxmd`; four offer written guidance instead. Deeper chip and firmware
probes exist in the module but are deliberately not run here, because they would
reset the board and steal its serial port.

## Type B health beacons

RTNode-2400 nodes can't run LXMF (embedded C++ Reticulum is core RNS only), so
they carry health in the `app_data` of a periodic RNS **announce** on the
`rtnode.health` aspect — a compact 14-byte, big-endian payload decoded by
[`monitor/health_beacon.py`](monitor/health_beacon.py). An **on-demand poll**
([`monitor/health_poll.py`](monitor/health_poll.py)) sends a 1-byte request
(`0x01`) to the node's destination; the node replies with an immediate beacon,
and a clean reply clears a node's warning back to green.

The encoder is pinned to the firmware by contract: `firmware/rtnode-2400/`
vendors the C++ headers, and a test compiles them with `g++` and asserts
byte-equality with the Python implementation.

## Monitoring

`ui/app.py` starts two things at launch: a 30-second poll cycle
(LAN `/status` discovery, `rnpath` mesh discovery) and an **RNS announce
listener** registered against the shared `rnsd`. One handler ingests every
announce into a persisted node registry, collapsing a device's several aspect
destinations into a single row by identity. A second collects `rtnode.health`
nodes as poll targets. Every announce heard is logged — that line is the ground
truth for "is the app deaf?", a question that once took hours to answer.

On a dev box with no RNS library installed, the listener returns quietly and the
rest of the app runs.

## Building and adopting nodes

- **SD-card imaging** ([`provisioning/pi_imager.py`](provisioning/pi_imager.py))
  writes a carried Raspberry Pi OS image to a USB card reader with `dd`, then
  mounts the boot partition and writes `custom.toml`, enables SSH and injects the
  medic's public key so the build workflow can log in afterwards. It refuses any
  target that is not a present, removable USB disk, and requires a typed
  confirmation. The image is expected at `~/pi_os_lite.img.xz`; there is no
  download path, and it fails with that message if absent.
- **Guided birth** walks the operator one instruction per screen: antenna first
  (never power a radio without one), then detect the board, read and classify it,
  then either adopt it or choose a path — host RNode, RTNode-2400, or Pi + radio.
  The Pi path hands off to the SD imager and back.
- **Adoption** enrols an already-provisioned node as kin without reflashing or
  renaming it: identify, certificate, enroll. It works over USB, and over the air
  for a node heard on the mesh but never plugged in.
- **Birth certificates** are photographable cards. Their QR payload is anonymous
  by construction — no location, builder, LAN details, mesh identity hash or
  notes.

## Map and placement

SCAN renders Web Mercator raster tiles from a local MBTiles file. Tiles are
**not shipped** — `assets/maps/` is empty and gitignored. They are downloaded
per-region while online (from the Carto CDN, never by bulk-fetching OSM), after
which the map is fully offline. With no tiles present the screen falls back to a
plain coordinate plot, and nodes without coordinates are listed below the map
rather than dropped.

Placement suggestions ([`monitor/placement.py`](monitor/placement.py)) mark
where a node would extend the mesh, from an `rnpath`-derived topology and a
log-distance path-loss model self-calibrated against this mesh's observed reach.
Suggestions appear only once `rnpath` returns edges between located nodes.

Distance alone is a poor predictor, and this mesh has the scar to prove it: a
sub-kilometre link failed because the path ran through houses, at a distance the model
called comfortable. So a suggestion is also checked against the ground
([`monitor/terrain.py`](monitor/terrain.py)) — it walks the elevation profile
between the candidate and each partner, adds the earth's curve at the standard
4/3 effective radius, and reports the tightest squeeze against the first Fresnel
zone. A blocked path becomes a caution naming the node it cannot see.

Three limits, stated in the code and worth repeating here:

- **Terrain is not buildings.** SRTM is bare earth, so a clear profile through a
  suburb is still a suburb. "Clear" means the ground does not block it, never
  that the link will work.
- A missing tile reads as **unknown**, never as sea level — otherwise a mountain
  looks like clear air.
- With no terrain cached at all, suggestions are returned unchanged with a note
  that they are distance-only. A field tool that withheld advice for want of a
  map would be worse than one that gives advice with a stated limit.

Terrain rides along with the map download — one button, one region, both
datasets — as z/x/y elevation tiles in the same scheme as the basemap, cached
beside it. It is a few dozen tiles against the basemap's thousands. A **Terrain**
toggle on SCAN shades high ground light and low dark, scaled to the range in
view rather than to absolute altitude, because what helps a radio is standing
above its surroundings: a 40 m rise in a flat suburb matters as much as a peak
does in the Alps.

## Privacy

Exact node locations are meant to stay on the builder's medic. Today that holds
in two places, and it is worth being precise about where it does not:

- Coordinates written into an RTNode-2400 over its setup portal are **fuzzed by
  800 m** first ([`monitor/geo.py`](monitor/geo.py)), deterministically per node
  and never centred on the true point, so repeated announces cannot be averaged
  back and the circle's centre is not the answer.
- The birth-certificate QR omits location entirely.
- The firmware then applies **its own** deterministic ~500 m offset on top, so a
  public pin sits up to ~1.3 km from the hardware
  ([`monitor.geo.public_pin_radius_m`](monitor/geo.py)). Both layers are
  deterministic, which is the property that matters — an offset re-rolled per
  announce could be averaged away by a patient observer.
- The exact fix **is** kept in the birth certificate on the medic, and the SCAN
  map and the navigation links plot it at full precision. There is no user-facing
  privacy toggle; the fuzz radius is a constant on one workflow.

## Languages

The UI is translatable ([`ui/i18n.py`](ui/i18n.py)) with offline, source-keyed
catalogues. **Four languages** are selectable — English (the source) plus
Spanish, French and German, **363 keys** each, kept at parity by a test. Settings
▸ Language stores the choice; it applies when Node Medic restarts, not live.

Coverage is partial and honest about it: roughly 344 wrapped call sites across 18
UI modules. Several screens (WiFi, storage, about, certificate view, the imager)
are still English-only, as are the descriptions under the Settings rows. An
AST-walking test asserts that every string that *is* wrapped has a translation,
so coverage cannot silently regress.

## Architecture

```
node_profile.py   dataclasses / enums — node roles, hardware, connection methods
transport/        how a command reaches a target: SSH, Local (on the medic,
                  incl. a PTY runner for rnodeconf), Serial, Emulated
diagnostics/      the check library — base classes + 8 category modules, 94 checks
workflows/        operations performed ON a node: build, flash, repair, adopt,
                  RTNode-2400 build, offline firmware/wheel caches, board catalogue
monitor/          the observation layer behind VITALS/SCAN: beacon codec, poll,
                  registry, history, alerts, topology, placement, triage feed,
                  the medic's own self-diagnosis
provisioning/     the medic's OWN configuration and the link it uses to reach a
                  node: display, power, storage, clock, identity, encrypt-at-rest
                  vault, SSH pinning, radio defaults, SD imaging, USB gadget, UART
ui/               the Kivy touchscreen app — shell, theme, widgets, 26 screens,
                  plus non-Kivy helpers (board detection, QR, i18n, geometry)
firmware/         vendored RTNode-2400 C++ headers, so the health-beacon wire
                  format sits beside its Python counterpart and is contract-tested
sandbox/          a Lima VM mirroring the medic's software surface, so sudo
                  scoping, SSH hardening and the vault can be broken safely
scripts/          systemd units, the boot/autostart installer, vault operator scripts
docs/             long-form notes — see below
tests/            1776 tests, headless
assets/           4 Reticulum config templates, 3 translation catalogues,
                  10 board photographs, UI artwork
```

Further reading in [`docs/`](docs/): [`RTNODE2400_INTEGRATION.md`](docs/RTNODE2400_INTEGRATION.md)
(the verified firmware↔tool contract), [`encrypt-at-rest.md`](docs/encrypt-at-rest.md),
[`TRACKER_GNSS.md`](docs/TRACKER_GNSS.md), and [`HANDOVER.md`](docs/HANDOVER.md)
(running project context).

## Development

Test-first throughout. The whole tested core runs headless with no hardware via
an in-memory `EmulatedConnection`, and never imports Kivy.

```bash
python3 -m pytest        # 1776 tests (2 skipped without hardware)
```

```bash
python3 main.py          # launch the touchscreen app (needs Kivy + a display)
python3 main.py --version
```

`main.py` enables `faulthandler` to `~/ui_crash.log` before starting the UI:
segfaults in Kivy/SDL/GL/serial C code kill the app with no Python traceback,
and a field crash has to be diagnosable after the fact.

**Emulated demos are opt-in.** On Linux — the deployed medic — flash, build,
Pi-build, PROBE and MITOSIS either do real work or fail with a stated reason;
they never report a fake success. Set `RNM_DEMO=1` to explore the flows with
emulated hardware and seeded demo nodes. SD imaging and both adoption paths are
never emulated.

## Status

Working and used in the field: the medic boots to the UI, hears the mesh live
through its own attached RNode, and monitors, maps, diagnoses and repairs real
nodes. Node building is proven end to end for Heltec V3/V4 RNodes, standalone
RTNode-2400 boards, and Pi transport nodes over a cable — including SD imaging,
guided birth and adoption.

Known gaps, stated plainly:

- **MITOSIS is not enabled.** Cloning the medic onto a fresh Pi refuses rather
  than faking it.
- **Map tiles and the Pi OS image must be pre-staged** while online.
- **Terrain is bare earth.** Line of sight is checked against ground only;
  buildings and trees are not modelled, and a clear profile through a suburb
  is still a suburb.
- **Translations are partial** and apply on restart.
- **Board coverage is uneven** — 9 of 15 boards have verified flash answers,
  10 of 15 have photographs, and only Heltec V3/V4 are hardware-verified.
- Location privacy is enforced on the node-provisioning path, not on the map.
- **GPS is not installed.** `gpsd`/`gpspipe` are absent, so "sync from GPS"
  cannot succeed; the workflow that would install them has no button yet.

## License

MIT — see [LICENSE](LICENSE).
