# Handover — read this first

Written 2026-08-15 at the end of a long session, for the next assistant.

Four documents, and you want all four:

- **This file** — the goal, the operator, the roadblocks, and what is done.
- **`docs/HANDOVER.md`** — the ORIGINAL running project context: the firmware
  contracts locked with `5ugAv/RTNode-2400`, the beacon codec, the portal field
  names, the open firmware-side issues. Older than this file and still the best
  reference for what was agreed with the firmware side. Do not overwrite it —
  the previous assistant nearly did, on the last night of the session.
- **`docs/WORKING_METHOD.md`** — the rules that were paid for, and Part 5, the
  previous assistant's own failures with the correction for each. Read that
  before you write code.
- **`docs/NEXT_BRIEFS.md`** — six specified jobs (A–F), ordered because they
  conflict.

---

## 1. What Node Medic is FOR

A Raspberry Pi 5 touchscreen field tool that **builds, monitors, diagnoses and
clones Reticulum LoRa mesh nodes**. It is carried, not installed. Its founding
principle, in the operator's words:

> Nodes stay useful and repairable when their keeper moves away or dies.

Everything follows from that. Nodes must be adoptable by someone who wasn't
there when they were built; the medic must be handable to someone else; and a
node in the wild must not be traceable to a person or a place.

**And it must work with no internet.** Not "degrade gracefully" — *work*. The
mesh exists for places and situations where infrastructure is absent. A tool
that needs a connection to build a node for a network whose purpose is not
needing a connection has missed its own point.

---

## 2. Offline-first: what already works, and what still breaks

### Solved — do not undo these

- **The cable birth.** A Pi is provisioned over a USB gadget link at
  `10.55.0.1/10.55.0.2`, no Wi-Fi anywhere in it. Proven end to end.
- **Carried firmware cache.** RNode firmware is flashed from a local cache;
  GitHub is only consulted when online (`workflows/updater.py`).
- **Carried Python wheels.** The build installs `rns` and its stack from
  wheels on the medic — no PyPI. `_ensure_pip` can even run pip out of the
  Debian wheel already on the image.
- **Carried Columba APK** for the operator's phone.
- **Time without a clock.** No RTC on a Pi; GPS from the onboard Tracker sets
  it, and Self Diagnose has a clock-sync check because a wrong clock after a
  power cut breaks TLS and confuses every log.

### Still broken — these are the roadblocks

**1. Geocoding needs the internet.** `monitor/geo.geocode_address()` calls
Nominatim. The location feature just specified (brief F) asks the operator to
type a place — *in the field that fails silently*. **Work through it:** the map
already caches tiles, so let them **pin on the map** instead of typing, and
accept typed coordinates directly. Typing an address should be the online
convenience, not the primary path.

**2. Map tiles must be pre-cached, and nothing prompts for it.** There is tile
download machinery (`ui/map_tiles.py`, `monitor/map_download.py`) but no step in
any flow says "you are about to go somewhere without internet — cache the tiles
for that area first". **Work through it:** a pre-trip check, on the same screen
as Home/Backpack mode. Backpack mode is the signal the operator is leaving.

**3. `apt-get` fallbacks cost 17 minutes of timeouts when offline.** The build
tries offline sources first (correct), but the last-resort apt path on a node
with no route burns a long time before failing. **Work through it:** detect
"no route" once, up front, and skip every network fallback for the rest of the
run rather than discovering it per-step.

**4. Publishing to a public map needs an uplink** by definition. The audit found
rmap.world runs a transport node for this. **This is not a bug** — just be
honest on screen that a LoRa-only node's position will not reach an internet
map, rather than implying it will.

**5. Refreshing anything carried needs periodic internet.** Firmware, APK,
wheels, tiles. **Work through it:** one "top up everything while you have a
connection" action, so the operator does it deliberately before leaving instead
of discovering a stale cache in the field.

---

## 3. How the operator wants to be given instructions

This matters more than it sounds. Get it wrong and the work stalls.

> "Can you simplify your instructions to dot point? That's it. Nothing else.
> I won't do anything that is not asked."

- **Bare actions, one per line.** No reasoning inside a step.
- **Reasoning goes BETWEEN steps**, or after, or in a hint — never mixed into
  the thing they are meant to do.
- **One task at a time when working a list.** Give the step, wait, then the
  next. Do not stack five and hope.
- **Board choice once, and one confirmation.** Do not re-ask.
- **Assume "yes, continue."** Standing instruction: *"if you have questions
  about progress, I want you to assume I want everything we are working on to
  keep moving forward until it is clean."* Finish, test, commit, deploy, verify
  without asking permission at each hop.
- **Never restart the medic's UI while it is working.** `scripts/restart_ui.sh`
  refuses during a flash or an SD write; a manual `pkill` does not.
- **Anything on GitHub that needs their account or a privacy decision:** ask
  them to do it at the Mac and walk them through it.
- They are usually **mobile or at the bench**, often with hardware in hand.
  Photos of the screen are their normal way of reporting. Read them.

---

## 4. Working process

Full detail in `docs/WORKING_METHOD.md`. The essentials:

### Two agents, different lenses — standing rule

Work that would go to one agent goes to **two, briefed from different angles**.
Identical briefs produce correlated answers, which is the whole thing to avoid.
Proven splits:

- **source vs consumer** — one reads the protocol for a format, the other reads
  how existing clients consume it
- **build vs break** — one implements, the other tries to make it fail
- **reader vs provenance** — one asks "what does a person need here", the other
  "what was this sentence written to prevent, and is it still true"
- **hardware-up vs code-down** — one traces sockets and power, the other traces
  the code path

**Findings merge. Designs do not.** Take every discovery from both; where they
designed the same screen twice, pick one spine and graft the good ideas across.

Skip the pair for mechanical work with an established pattern — you will just
throw one away.

**This method found**, in one night: five false sentences on the birth screens,
four screens overflowing the panel, two dead impossibility checks, and the fact
that the location fuzz is invertible. Both wording agents independently deleted
the same false sentence. That agreement is the signal.

### Non-negotiables

- **Commit before launching an agent.** A worktree snapshots `main` at launch;
  anything committed after is invisible to it. Uncommitted work is work that did
  not happen — the previous assistant learned this the hard way, twice.
- **Every file-editing agent runs isolated.** One launched without isolation
  edited the main tree live, 25 files.
- **Always ask the agent to verify the brief against the code** and report
  contradictions. This is the single practice that catches stale claims.
- **The airlock** (`.git/hooks/`) gates commits on `main` and deploys to the
  medic while agent work is unreviewed. Override is `AIRLOCK_REVIEWED=1`,
  per-command, after reading the diff. It is self-attesting — an agent stepped
  past it once. Treat it as a reminder, not a wall.

---

## 5. State

**Done and on the medic:** the node-detail crash that killed the app on a VITALS
tap; a cached path no longer counted as a sighting (verified live); the `/status`
server and build steps that prove a node reports; one VITALS row per machine.

**Done, on `main`, not deployed:** the location-sharing model, the vault factor
model (maximum strength), the nine-dot pattern pad, the first-use setup wizard,
the birth wording rewrite (980→616 words, font +25%).

**Unproven:** the health-beacon fix. `set_default_app_data(current_beacon)` is
committed and deployed and **has never been observed landing a beacon.** The
next rebirth is the test.

**The privacy work is complete and published** — history rewritten, the repo is
clean of the operator's home coordinates, SSID, username, and a collaborator's
name. Verified against `origin` after the force-push.

**The open jobs are A–F in `docs/NEXT_BRIEFS.md`.** Order matters; they touch
the same three files. **F (the location fuzz) is the most urgent** — it is the
only place the tool currently tells an operator something false about their own
safety.

---

## 6. The one habit that matters most

When the tool and the hardware disagree, **believe the hardware**, then find out
why the tool was wrong. It is almost never where you first look.

And say only what you have checked. The operator holds that standard, holds you
to it, and it is why the hard bugs in this project get found at all.
