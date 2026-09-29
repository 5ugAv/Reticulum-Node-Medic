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
- [ ] **Chat / communication** — messages actually move

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

- Text to speech — the medic has no speaker; revisit if hardware changes
- Arabic and right-to-left layout — needs RTL work first
- More boards past the current 16
- Phone companion app
- Front-page mode renaming beyond the artwork itself
