# Front page — artwork brief

**For whoever draws it.** The Node Medic front page is a single painted poster
(`assets/ui/front_page.png`). The tool draws no buttons: it displays the image
and converts taps into image-fraction coordinates. So the words on the picture
ARE the interface, and changing them is an artwork job, not a code job.

Written 2026-09-11, after the operator settled the vocabulary. This is the
single largest barrier to anyone but the builder using the tool
(`docs/V1_SCOPE.md`), and it has a lead time — it is the long pole for v1.

---

## Why it is changing

BIRTH, TRIAGE and MITOSIS are a private language. A stranger holding the tool
cannot guess what they do, and MITOSIS is the one nobody gets. The subtitles
already do the explaining; this makes the word itself do it.

## The words

Five cards, left to right, unchanged in order and position:

| Position | Now | Becomes | Subtitle |
|---|---|---|---|
| 1 | VITALS | **VITALS** (keep) | CHECK NODE HEALTH |
| 2 | SCAN | **MAPS** | VIEW NODES & TOPOLOGY |
| 3 | BIRTH | **BUILD** | NEW NODES & LINKS |
| 4 | TRIAGE | **ANTENNA** | SIGNAL & PLACEMENT |
| 5 | CHAT | **CHAT** (keep) | CONNECT & DISCUSS |

Two words do NOT get a card:

* **PROBE** — per-node repair is reached by tapping a node in VITALS, so it
  needs no front-page entry. (Its one medic-level function, Self Diagnose,
  is reached from the gear. See "Open question" below.)
* **Clone this device** (was MITOSIS) — it lives INSIDE the BUILD screen,
  because cloning is once in a device's life, not a weekly mode. **Already
  done in code** — no artwork needed.

## Keep the illustrations

Each card's existing artwork stays with its position. Only the word changes.
MAPS keeps the map-and-pin, BUILD keeps the tower-and-crane, ANTENNA keeps the
dish-and-hills. They are recognisable and operators already navigate by them.

## Geometry — measured, not estimated

Taken from `ui/home_zones.py`, which is the tap-map the code uses:

* **Canvas: 720 x 1280** — the panel's native size. Do not change it.
* **Card row: y 1011 to 1280** (the bottom 269 px), **full bleed** left to right
* **Five equal columns, 144 px each.** The tap-map divides the row by five and
  nothing else — a card that drifts from its column becomes untappable.
* **Red cross emblem: centre (360, 589), radius 94 px.** It is an Easter egg
  (opens the credits). Keep it where it is or the egg moves.
* **Wi-Fi emblem: x 302–418, y 320–429.** Also a tap target (Wi-Fi settings).
* Top-right and top-left corners carry the gear and the power slider, drawn by
  the app OVER the poster. Keep those corners clear of anything tappable.

## Style

Match the existing poster exactly — same palette, same worn-print treatment,
same type. This is one picture that happens to be getting five words changed,
not a redesign.

## What happens when the art lands

Drop the new PNG in at the same path and these follow, in one commit:

* `ui/home_zones.py` — `POSTER_CARD_LABELS` (a test holds it in step with the
  painting, deliberately, so the words and the map cannot drift apart)
* the first-use tour, which crops the real painted card out of the poster to
  show a newcomer where to tap
* the ~40 places in the UI that still say the old words in prose
* the 11 translation catalogs

## Open question for the operator

**Self Diagnose** is the medic's own 11-check health run — it is about the tool
in your hands, not about a node, so it does not belong under VITALS with the
per-node repairs. Today it is only reachable from the sidebar. The gear is the
natural home for it. Decide before the art is drawn, in case it wants a word on
the poster after all.
