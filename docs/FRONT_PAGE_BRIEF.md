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
| 1 | VITALS | **VITALS** (keep) | NODE HEALTH |
| 2 | SCAN | **MAPS** | SEE THE MESH |
| 3 | BIRTH | **BUILD** | MAKE A NODE |
| 4 | TRIAGE | **ANTENNA** | CHECK SIGNAL |
| 5 | CHAT | **CHAT** (keep) | SEND MESSAGES |

Subtitles are capped at ~13 characters so each one holds a SINGLE line at 19 px
in a 144 px column. This is not a style preference. The 2026-09-26 revision used
longer wording ("VIEW NODES & TOPOLOGY", "SIGNAL & PLACEMENT"); three of the five
broke to two lines, the point size dropped to 15.3 px to fit, and under glance
blur those three smeared to unreadable grey while the two single-line captions
stayed legible. Shorter caption, bigger type, readable card. Any rewording that
pushes a subtitle onto a second line undoes that, so count the characters first.

## The title block, as painted 2026-09-28

    RETICULUM
    NODE MEDIC
    COMMUNICATION NETWORK BUILDING TOOL

The subtitle gained "COMMUNICATION" on the operator's call: a stranger reading
"NETWORK BUILDING TOOL" on a green screen can reasonably think routers and
switches. The word says which kind of network before anyone has to guess.

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

## Self Diagnose — DECIDED (operator, 2026-09-13)

**It stays under VITALS.** The operator's reasoning, verbatim in spirit:
VITALS is where everything gets checked for health, and Self Diagnose is the
same act pointed at the medic itself — the medic is just another patient.
The gear proposal above is dead; no poster word needed.

Already true in code: VITALS' filter row carries the Self-check button
(`ui/screens/vitals_screen.py`, wired in `ui/app.py`). This section exists so
nobody relocates it to the gear on the strength of the paragraph that used to
sit here.
