# Where the microSD slot is, per Raspberry Pi model

Reference data for animating a card sliding into the slot. Written because the
operator's standing rule is that *"all images the user sees on Node Medic should
correspond to the hardware they've got in their hand"* — an animation that shows
the card entering the wrong edge, the wrong face, or with a click that the board
does not have is the same class of bug as showing the wrong board photo.

Second standing rule that governs this page: **research each board individually.**
Nothing below is carried across from one Pi to another. Every row names the
evidence it rests on, and the `Verified` column says plainly what was measured
and what was inferred.

---

## 1. The table

Board dimensions from the official Raspberry Pi mechanical drawings. `v` is the
position of the slot **along the edge it sits on**, measured from the GPIO-header
edge, as a fraction of that edge's length.

| Model | Board | Slot edge | `v` (from GPIO edge) | In mm | Face | Card goes in | Retention |
|---|---|---|---|---|---|---|---|
| `pi_zero_2w` | 65 × 30 mm | left short edge (30 mm), opposite the CSI camera connector | **0.41** | 12.3 mm of 30 | **TOP** (component side) | label **UP**, contacts **DOWN** | friction — no click |
| `pi_3a_plus` | 65 × 56 mm | left short edge (56 mm), opposite the USB-A port | **0.50** | 28.0 mm of 56 | **UNDERSIDE** | label **DOWN**, contacts **UP** | friction — no click |
| `pi_3b_plus` | 85 × 56 mm | left short edge (56 mm), opposite USB/Ethernet | **0.48** ±0.02 | ≈27 mm of 56 | **UNDERSIDE** | label **DOWN**, contacts **UP** | friction — no click |
| `pi_4b` | 85 × 56 mm | left short edge (56 mm), opposite USB/Ethernet | **0.49** | 27.5 mm of 56 | **UNDERSIDE** | label **DOWN**, contacts **UP** | friction — no click |
| `pi_5` | 85 × 56 mm | left short edge (56 mm), opposite USB/Ethernet | **0.49** | 27.7 mm of 56 | **UNDERSIDE** | label **DOWN**, contacts **UP** | friction — no click |

"Left edge" assumes the board drawn the way every product photo draws it: GPIO
header along the top, USB/Ethernet on the right.

### The headline

**Four of the five are the same animation.** On every 56 mm-edge Pi — 3 A+, 3 B+,
4 B, 5 — the slot is *centred on the left short edge* (0.48–0.50, i.e. within
about 1.5 mm of the midpoint) and *on the underside*. The Zero 2 W is the odd one
out on both counts: higher up its edge (0.41), and on the **top** face.

That top-vs-underside split is the fact that actually matters for "the operator
holds the same thing they see on screen", because it flips which way up the card
goes.

---

## 2. Slot geometry details

Common to all five (measured off the photographs listed in §5):

* The socket body is **11.5–12 mm** wide along the edge (that is the microSD's
  11 mm width plus the holder walls) and **12–13 mm** deep into the board.
* The card protrudes roughly **3 mm** past the PCB edge when fully seated. It has
  to: with a friction socket that overhang is the only thing you can grip.
* Zero 2 W: the socket mouth sits **flush** with the PCB edge (≈0.5 mm inboard).
* 3 A+ / 3 B+ / 4 B / 5: the socket mouth sits **≈2.5–3 mm inboard** of the PCB
  edge, so the card crosses a short stretch of bare board before it enters.

### Retention: none of the five click

The push-push (spring-eject) socket was dropped at the **Raspberry Pi 3 Model B**,
in 2016, and no supported model has had one since. Raspberry Pi's jamesh, in the
forum thread below, gives the reason: on the B+, A+ and 2 B the spring could
release the card unexpectedly and take the system down with it.

So the animation must **not** play a click, snap, or spring-back on any of the
five models. The card goes in and stays where it is put. The Pi 2 B and earlier
did click — but the tool does not support them.

### Which way up

Raspberry Pi's own getting-started documentation gives the rule that covers every
model at once:

> "insert it into the microSD slot with the label facing away from the Raspberry Pi"

Contacts always face the PCB. What changes is which way that points:

* **Zero 2 W** — socket on the top face, so with the board component-side up the
  label faces **up at you** and the gold contacts face **down** into the board.
* **3 A+ / 3 B+ / 4 B / 5** — socket on the underside, so with the board
  component-side up the label faces **down at the table** and the contacts face
  **up** into the board.

**Consequence for the artwork, and it is not cosmetic:** the existing card
sprite (`sd_card_endurance.png`, §4) is the *label* face. It can be used
unmodified for the Zero 2 W. For the other four, a top-down view of the board
would honestly have to show the card's *contact* face, not its label. Either flip
to an underside view of the board for those, or draw the card from behind — do
not show a label-up card sliding into a Pi 4 that is drawn component-side up,
because that is the operator doing it wrong.

---

## 3. Where the slot lands on the artwork we actually ship

The `v` fractions above are of the *physical board*. The art is cropped
differently in every file, so these are the fractions of the **image**, which is
what an animation needs. Derived by locating the 3.5 mm mounting holes (which are
at v = 0.0625 and v = 0.9375 on every model) and solving for the board rectangle.
Tolerance ±0.01.

| Asset | Size px | Board rect (fractions of image) | Slot mouth (x, y) |
|---|---|---|---|
| `assets/ui/anim/pi_zero_2w.png` | 1700 × 925 | x 0.066→0.925, y 0.094→0.825 | **(0.066, 0.40)** |
| `assets/ui/anim/pi_zero_2w_cut.png` | 1508 × 710 | fills the frame | **(0.005, 0.41)** |
| `assets/boards/pi_3a_plus.png` | 1536 × 1024 | left edge x 0.088; y −0.014→0.975 | **(0.088, 0.481)** |
| `assets/boards/pi_4b.png` | 1536 × 1024 | left edge x 0.086; y −0.008→0.887 | **(0.086, 0.431)** |
| `assets/boards/pi_5.png` | 1536 × 1024 | left edge x 0.090; y 0.001→0.932 | **(0.090, 0.461)** |

**Warning about the three `assets/boards/` Pi photos:** they are cropped tight and
the board **bleeds off the frame** — the GPIO edge is clipped at the top and the
right-hand and bottom edges are outside the image entirely. That is why the board
rects above have negative or >1 bounds. Only the **left** PCB edge — the edge the
card enters — is fully in frame on all three, which happily is the one the
animation needs. The Zero asset is the only one with margin all round.

The Zero asset is dimensionally faithful: its board rect is 2.16:1 against the
true 65:30 = 2.167, and the drawn socket measures 11.6 mm × 12.6 mm against a real
holder's ~11.5 × 13 mm. It can be measured against directly.

---

## 4. The card illustration to reuse

Do not draw a new card. This one already appears in the imaging screens:

| | |
|---|---|
| **Path** | `assets/ui/anim/sd_card_endurance.png` |
| **Size** | **1328 × 1003 px**, RGBA (background already keyed out) |
| **What it is** | A high-endurance microSD, **label face**, drawn from the operator's own card and rebranded (`scripts/rebrand_sd_card.py`): no manufacturer's mark, Node Medic's own name, and 32 GB — the size a propagation node should be built on |
| **Orientation in the file** | long (15 mm) axis **horizontal**; the **contact end points RIGHT**; chamfered corner top-right |
| **Referenced as** | `SD_ENDURANCE_PNG` in `ui/widgets/birth_anims.py`, also used by `ui/widgets/surgery_anim.py` |

The contact end already points right, which is the direction of travel for a slot
on a board's left edge — every one of the five. No rotation needed for the Zero 2 W.

There is a second, older sprite, `assets/ui/anim/sd_card.png` (**477 × 474 px**,
RGBA). Leave it alone: `birth_anims.py` notes its geometry is measured against the
insert-into-the-**medic** animation, not into a Pi.

---

## 5. Evidence — what was verified, and how

Per the standing rule, board by board. Nothing here was carried over from another
model.

| Board | How the slot position was established | Verified or inferred |
|---|---|---|
| Zero 2 W | Measured off `assets/ui/anim/pi_zero_2w.png`, which carries the **RP3A0-AU** silkscreen (that SiP is on the Zero 2 W and nothing else). Socket visible on the top face. Scale cross-checked: drawn socket = 11.6 × 12.6 mm. Independently corroborated by a real Zero-family top photo showing the socket on the component side with "MICRO SD CARD" silkscreen. | **Verified** (position + top face). Retention type **inferred** — see below. |
| 3 A+ | Measured off a straight-on **underside photograph** carrying **FCC ID: 2ABCB-RPI3AP**, which is the 3 A+ and nothing else. Located against its own 3.5 mm mounting holes → v = 0.4995. Corroborated by the official 3 A+ **case** drawing, whose microSD aperture sits mid-way along that face. | **Verified** |
| 3 B+ | Measured off an underside photograph carrying **FCC ID: 2ABCB-RPI3BP**. Located against the mounting holes → v = 0.476. The photo is hand-held with visible tilt, hence the ±0.02. | **Verified, lower precision** |
| 4 B | Measured off a straight-on underside photograph **with a card seated in the slot**, silkscreened "MICRO SD CARD". Located against the mounting holes → v = 0.491. Board aspect checked at 1.525 against the true 85:56 = 1.518. | **Verified** |
| 5 | Measured off a CC BY-SA underside photograph, silkscreen **"SD CARD" / J9**, FCC ID 2ABCB-RPI5. Located against the mounting holes → v = 0.494. | **Verified** |
| all five | Board outline dimensions, mounting-hole positions | **Verified** against the official Raspberry Pi mechanical drawings |
| all five | Card orientation (contacts toward the PCB) | **Verified** against Raspberry Pi's own getting-started text, combined with the per-board face established above |
| Zero 2 W, 3 A+, 3 B+, 4 B, 5 | Friction, not push-push | **Verified for the Pi 3 family and later** from the Raspberry Pi forum thread where a Raspberry Pi engineer states the change and its reason. **Inferred for the Zero 2 W**: the Pi Zero is documented friction-fit and the 2 W reuses the same footprint and holder family — not independently confirmed for the 2 W itself. It is the safe direction to be wrong in, since it means the animation omits a click rather than inventing one. |

### Things checked and found NOT to be true

* **"Older ones were push-push, the Zero 2 W and Pi 4/5 are friction."** No. The
  change happened at the **Pi 3 Model B**, so *all five* supported models are
  friction. There is no board in this tool's list that clicks.
* **"The Pi 4's slot is down by the USB-C corner."** No — it is centred on the
  edge, same as the 3 A+, 3 B+ and 5.
* **"The Pi 5 moved the slot to the other side of the board."** Repeated by
  several secondary sources; the photograph does not support it. The Pi 5's slot
  is on the same edge, at the same place (v = 0.494 vs the Pi 4's 0.491).

### Sources

* Official mechanical drawings — [Zero 2 W](https://datasheets.raspberrypi.com/rpizero2/raspberry-pi-zero-2-w-mechanical-drawing.pdf), [3 A+](https://datasheets.raspberrypi.com/rpi3/raspberry-pi-3-a-plus-mechanical-drawing.pdf), [3 B+](https://datasheets.raspberrypi.com/rpi3/raspberry-pi-3-b-plus-mechanical-drawing.pdf), [4 B](https://datasheets.raspberrypi.com/rpi4/raspberry-pi-4-mechanical-drawing.pdf), [5](https://datasheets.raspberrypi.com/rpi5/raspberry-pi-5-mechanical-drawing.pdf). These are **top views only** — the underside sockets do not appear on them, which is why photographs were needed.
* Official [3 A+ case mechanical drawing](https://datasheets.raspberrypi.com/case/raspberry-pi-3-a-plus-case-mechanical-drawing.pdf) — shows the microSD aperture.
* Card orientation: [Raspberry Pi getting-started documentation](https://www.raspberrypi.com/documentation/computers/getting-started.html).
* Retention type: [Raspberry Pi forum, "Raspberry Pi 3 microSD card slot - spring ejection?"](https://forums.raspberrypi.com/viewtopic.php?t=140658) (Raspberry Pi engineer jamesh on why the spring was dropped).
* Underside photographs: Pi 5 — [Wikimedia Commons, CC BY-SA 4.0, Suyash Dwivedi](https://commons.wikimedia.org/wiki/File:Raspberry_Pi5_8GB_Bottom_View_(1).jpg); 3 B+ — [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Pie_3_B_Plus_back.gif); 3 A+ — [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Raspberry_Pi_3_A%2B_(46703825431).jpg); 4 B — CNX Software Raspberry Pi 4 review. Used for **measurement only**; none of them were copied into the repo.

---

## 6. Notes on the board artwork itself

Checked by silkscreen, as required before filing or trusting any board image:

| File | Silkscreen read | Verdict |
|---|---|---|
| `assets/ui/anim/pi_zero_2w.png` | `RP3A0-AU` on the SiP | **Correct** — RP3A0 is the Zero 2 W's package and appears on no other board |
| `assets/boards/pi_3a_plus.png` | `Raspberry Pi 3 Model A+` / `Raspberry Pi 2019` | **Correct** |
| `assets/boards/pi_4b.png` | `Raspberry Pi 4 Model B` / `Raspberry Pi 2018` | **Correct** |
| `assets/boards/pi_5.png` | `Raspberry Pi 5` | **Correct** |

Two things worth knowing:

1. **`pi_zero_2w.png` is not missing.** `ui/board_images.image_for_pi()` searches
   `assets/boards/` *and* `assets/ui/anim/`, and the Zero 2 W art has been sitting
   in the second of those since before the boards convention existed. The birth
   flow already illustrates the Zero 2 W. Nothing needs to be sourced, and a
   second copy under `assets/boards/` would only create a file that can drift out
   of step with the one the animations use.

2. **The three `assets/boards/` Pi photos have garbled silkscreen** elsewhere on
   the board — `BROAUCOM`, `Made in the NK`, `Raspberry P1 4 Model 9`,
   `MAT+ CPTO TNTCRFBCE`. The *model name* is legible and right on each, so none
   of them is the wrong board, and they pass the test that matters. But they look
   like upscaled or generated imagery rather than clean photographs, and an
   operator reading the small print will find nonsense there. Flagged, not fixed —
   replacing them is a separate decision.
