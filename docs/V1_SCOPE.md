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

- [ ] **Birth** — a node built end to end, on the boards already working
- [ ] **Monitoring** — nodes appear, and their health comes back over the mesh
- [ ] **Maps** — placement and the node map
- [ ] **Antenna guide** — the choosing and testing path
- [ ] **Chat / communication** — messages actually move

Plus the one comprehension blocker, which is artwork and has a lead time:

- [ ] **A front page a stranger can read.** BIRTH / TRIAGE / MITOSIS is a
      private language. This is the single largest barrier to anyone but the
      builder using the tool, and it cannot be fixed in code — the words are
      painted into `assets/ui/front_page.png`.

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
