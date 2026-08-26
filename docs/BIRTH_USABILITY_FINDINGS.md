# BIRTH usability findings — three-persona walkthrough (2026-08-26)

Marnie (68, never used a terminal), Dev (impatient skimmer), Tomas (careful,
solo) each walked all three births. Verdicts:

| Flow | Marnie | Dev | Tomas |
|---|---|---|---|
| RNode (radio) | no | yes* | **yes** |
| RTNode-2400 | no | yes** | stall/no |
| Pi + RNode | no | probably | probably |

\* if the board auto-identifies  \*\* if it auto-IDs **and** the medic is on Wi-Fi

The birth flow does a lot RIGHT — protect these: one physical action per screen
with auto-advance; hard refusal to flash the medic's own radio (Jonesey/Tracker
excluded + `assert_flashable`); safe-by-default sliders; the board-aware
SD-into-Pi flip animation (everyone praised it); the radio-works-first gate;
honest "give it two minutes, nothing is wrong" closers.

## The recurring blockers (all three personas)

1. **The medic makes the beginner identify hardware it should identify.**
   "Which board is this?", "check the silkscreen" (V3/V4/XIAO under a *brick*
   warning), "Which Raspberry Pi is this?", "the radio not the Pi". This is THE
   theme. Direction: auto-detect where possible; where genuinely ambiguous show
   the *physical distinguishing mark as a photo* and never put brick-risk on the
   user. **(design + photos)**
2. **"Antenna on →" can power an un-antennaed radio** = hardware damage. The one
   place the biggest button is dangerous. Direction: an explicit "antenna is
   fitted" confirm before the board is powered — *tradeoff:* friction on every
   birth for the experienced operator. **(operator's call)**
3. **RTNode manual captive-portal onboarding** ("join 'RTNode-Setup' Wi-Fi, open
   http://10.0.0.1, type radio settings") when the medic isn't on Wi-Fi —
   un-completable solo. Direction: don't land a beginner there; either flag up
   front "this build needs Node Medic on Wi-Fi", or guide the portal on-medic.
   **(design)**
4. **Self-advancing screens with no button look "hung"** → nervous users pull
   hardware mid-flash/provision. Direction: a visible heartbeat + elapsed-time
   so "watching…" clearly looks alive. **(low-risk, safe to implement)**
5. **Silent, identical failures** — charge-only cable, wrong socket, missing
   card all look like "still waiting". Direction: after ~15–60 s of nothing, a
   plain "not seeing it — usually the cable/socket, here's the picture".
   **(low-risk, safe to implement)**
6. **No "what you need" pre-flight for births** (parts, Wi-Fi password, spare
   cable) — same gap just fixed for mitosis. **(low-risk)**
7. **Jargon at the finish** — "CONFIG MODE", "build log", "portal", and telling
   the keeper to "watch VITALS" for a bare RNode that never beacons. Direction:
   plain-language finish; for an RNode say "this plugs into your phone; it won't
   show in VITALS". **(low-risk copy)**
8. **No photos of the actual parts/sockets** (only two Heltec boards + the
   SD-into-Pi animation have real pictures). **(needs photos)**

## Suggested order
- Safe to implement now (no operator tradeoff): 4 (heartbeat), 5 (silent-failure
  nudge), 6 (birth pre-flight), 7 (finish copy).
- Operator's call (friction / design): 2 (antenna confirm), 3 (captive-portal),
  1 (hardware auto-ID strategy).
- Needs real photos from the operator: 1 and 8.
