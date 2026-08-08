"""The SD-card handover's geometry: which edge, which face, which way up.

These guard the DATA and the pure kinematics rather than pixels, because that
is where this step can go quietly wrong: an animation with a plausible-looking
card sliding into the wrong edge of a board looks fine in a screenshot and is a
lie to the person holding the hardware.

Kivy is not importable here (CI has none, and the suite installs partial stubs),
so nothing below imports the widget — ui.pi_sd_geometry is deliberately pure,
and the widget is checked by source inspection the way the connect-Pi art is.
"""

import os

import pytest

from tests.srcutil import func_source, src
from ui import board_images, pi_sd_geometry as sdgeo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WIDGET = "ui/widgets/birth_anims.py"
SCREEN = "ui/screens/birth_guide_screen.py"

STAGE = (0.0, 0.0, 760.0, 150.0)      # the wizard's stage on the 5" panel


def _pil():
    return pytest.importorskip("PIL.Image", reason="Pillow not installed")


def _aspects():
    """Sprite aspects for the scene, from the real files."""
    Image = _pil()
    out = {}
    for name, rel in (("card", "assets/ui/anim/sd_card_endurance.png"),
                      ("reader", "assets/ui/anim/sd_reader_body.png"),
                      ("medic", "assets/ui/anim/node_medic_body.png")):
        im = Image.open(os.path.join(ROOT, rel))
        out[name] = im.width / float(im.height)
    return out


def _layout(key):
    """The scene laid out for *key*, exactly as the widget lays it out."""
    Image = _pil()
    geo = sdgeo.geometry_for(key)
    path = sdgeo.sprite_path(geo) if geo else None
    if not path:
        pytest.skip(f"no artwork ships for {key}")
    a = _aspects()
    pi = Image.open(path)
    sl, sr = (0.10, 0.505), (0.50, 0.705)         # InsertSdAnim's slot points
    slot_frac = (((sr[0] - sl[0]) * a["reader"]) ** 2 + (sr[1] - sl[1]) ** 2) ** 0.5
    return geo, sdgeo.handover_layout(STAGE, geo, pi.width / float(pi.height),
                                      a["card"], a["reader"], slot_frac,
                                      a["medic"])


# --------------------------------------------------------------------------- #
# the data
# --------------------------------------------------------------------------- #

def test_every_pi_the_flow_can_offer_has_geometry():
    """The build flow asks "Which Raspberry Pi is this?" and the answer drives
    the picture. A model on that list with no geometry falls back to a generic
    outline, which is honest but silent — so at least make the omission
    deliberate rather than accidental."""
    for key in board_images.pi_art_status():
        assert sdgeo.geometry_for(key) is not None, f"no slot geometry for {key}"


def test_the_fields_are_all_in_range():
    for key, geo in sdgeo.PI_SLOTS.items():
        assert geo.edge in sdgeo.EDGES, key
        assert geo.face in sdgeo.FACES, key
        assert geo.retention in sdgeo.RETENTIONS, key
        for field in ("along", "mouth", "inset"):
            v = getattr(geo, field)
            assert 0.0 <= v <= 1.0, f"{key}.{field} = {v}"
        assert geo.mouth > 0.02, f"{key}: a slot mouth that small is a typo"
        # the box MAY fall outside the frame: three of the four photos are
        # cropped tight enough that the board bleeds off the edge
        x0, y0, x1, y1 = geo.board_box
        assert x0 < x1 and y0 < y1, key
        long_mm, short_mm = geo.board_mm
        assert 20.0 < short_mm < long_mm < 200.0, key


def test_an_unknown_model_gets_nothing_rather_than_somebody_elses_board():
    """The rule the whole module exists for. A Pi we cannot name must not
    inherit another model's slot — the card would enter an edge that board has
    nothing on, while looking entirely convincing."""
    assert sdgeo.geometry_for("") is None
    assert sdgeo.geometry_for("pi_400") is None
    assert sdgeo.geometry_for(None) is None


def test_the_a_and_full_supply_pi_5s_share_one_slot():
    """pi_5 / pi_5_full is a difference of POWER SUPPLY, not of board."""
    assert sdgeo.geometry_for("pi_5_full") is sdgeo.geometry_for("pi_5")


def test_every_entry_names_its_evidence():
    """All five were measured board by board (docs/pi_sd_slot_geometry.md),
    each against its own photograph and its own mounting holes, so none is
    flagged provisional any more. What must survive is the note saying where
    each number came from — a bare number with no provenance is exactly what
    the research replaced."""
    assert sdgeo.provisional_keys() == ()
    for key, geo in sdgeo.PI_SLOTS.items():
        assert len(geo.note) > 30, f"{key} has no note saying where it came from"


def test_not_one_supported_board_clicks():
    """The push-push (spring-eject) socket was dropped at the Pi 3 Model B in
    2016, so no board this tool supports has one. An animation that clicks or
    springs back would be claiming something the hardware does not do — and the
    operator would push a seated card expecting it to eject."""
    for key, geo in sdgeo.PI_SLOTS.items():
        assert geo.retention == "friction", f"{key} claims a click"


def test_four_of_the_five_are_one_animation():
    """The research's headline, worth pinning: every 56 mm-edge Pi puts the
    slot on the middle of its left short edge, underside. The Zero 2 W is the
    outlier — and it differs on the FACE, which is what flips which way up the
    card goes."""
    for key in ("pi_3a_plus", "pi_3b_plus", "pi_4b", "pi_5"):
        geo = sdgeo.PI_SLOTS[key]
        assert geo.edge == "left" and geo.face == "underside", key
        assert 0.45 <= geo.along <= 0.55, f"{key}: not centred on its edge"
        assert geo.card_face_up is False, key
    zero = sdgeo.PI_SLOTS["pi_zero_2w"]
    assert zero.edge == "left" and zero.face == "top"
    assert zero.card_face_up is True


def test_the_pi_zero_keeps_the_numbers_that_were_tuned_on_the_live_screen():
    """A regression guard on the one board that IS measured.

    The old code held three canvas fractions against pi_zero_2w.png — slot at
    0.085, cage 0.245→0.545 — plus a 0.013 downward nudge the operator arrived
    at by looking at the panel. Re-expressed against the board box they must
    still come back to the same place, or the step they approved has moved.
    """
    geo = sdgeo.PI_SLOTS["pi_zero_2w"]
    x0, y0, x1, y1 = (0.0647, 0.0951, 0.9324, 0.8205)      # the UNCUT sprite
    bw, bh = x1 - x0, y1 - y0
    assert abs((x0 + geo.inset * bw) - 0.085) < 0.004
    assert abs((y0 + geo.along * bh) - (0.395 + 0.013)) < 0.004
    assert abs(geo.mouth * bh - (0.545 - 0.245)) < 0.004


@pytest.mark.parametrize("key", sorted(sdgeo.PI_SLOTS))
def test_the_board_box_matches_the_artwork_it_names(key):
    """THE invisible-padding guard, in the two ways that still work.

    Every assets/boards photo carries ~145 px of transparency down its left
    side — 9% of the canvas. Fractions taken against the canvas would put the
    card's entry point off the board entirely: the same class of fault as the
    sprite with 82 px of hidden padding that threw an earlier animation out.

    The whole box cannot simply be re-measured from the pixels, because three
    of these photos are cropped tight and the board runs off the frame. But two
    things can be, and between them they catch a re-crop: the LEFT PCB edge,
    which is in shot on every one of them and is the only edge any of these
    boards puts a slot on; and the box's ASPECT against the board's real
    millimetres, which goes wrong the moment the crop moves.
    """
    Image = _pil()
    geo = sdgeo.PI_SLOTS[key]
    path = sdgeo.sprite_path(geo)
    if not path:
        pytest.skip(f"{geo.sprite} does not ship")
    im = Image.open(path).convert("RGBA")
    w, h = im.size
    px = im.load()
    cols, rows = [0] * w, [0] * h
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b, a = px[x, y]
            if a > 128 and g > r + 18 and g > b + 18:       # PCB green
                cols[x] += 1
                rows[y] += 1

    def span(arr):
        thr = max(1, int(0.02 * max(arr)))
        idx = [i for i, v in enumerate(arr) if v > thr]
        return idx[0], idx[-1] + 1
    x0, _x1 = span(cols)
    assert abs(x0 / w - geo.board_box[0]) < 0.02, (
        f"{key}: the box puts the left PCB edge at {geo.board_box[0]:.4f}, the "
        f"pixels say {x0 / w:.4f} — was {geo.sprite} re-cropped?")
    bx0, by0, bx1, by1 = geo.board_box
    drawn = ((bx1 - bx0) * w) / ((by1 - by0) * h)
    real = geo.board_mm[0] / geo.board_mm[1]
    assert abs(drawn - real) / real < 0.04, (
        f"{key}: the box is {drawn:.3f}:1 but a {geo.board_mm[0]:.0f}x"
        f"{geo.board_mm[1]:.0f} mm board is {real:.3f}:1")


# --------------------------------------------------------------------------- #
# the pure geometry
# --------------------------------------------------------------------------- #

def test_the_entry_direction_follows_the_edge():
    """A slot on the left is entered moving right, and so on round. The card
    sprite's leading edge points +x, so this angle IS the sprite's rotation —
    which is what keeps one piece of drawing code correct for four edges."""
    mk = dict(along=0.5, mouth=0.2, inset=0.05, face="top",
              retention="friction", card_face_up=True, sprite="x",
              board_box=(0, 0, 1, 1), board_mm=(85.0, 56.0))
    for edge, vec, angle in (("left", (1, 0), 0.0), ("bottom", (0, 1), 90.0),
                             ("right", (-1, 0), 180.0), ("top", (0, -1), 270.0)):
        geo = sdgeo.SlotGeometry(edge=edge, **mk)
        assert sdgeo.entry_vector(geo) == vec
        assert abs(sdgeo.entry_angle(geo) - angle) < 1e-6


def test_the_slot_mouth_sits_on_the_edge_it_names():
    """Each edge's mouth must land on THAT side of the board, near enough to
    the edge to read as its opening."""
    for key, geo in sdgeo.PI_SLOTS.items():
        bx, by, bw, bh = 100.0, 50.0, 300.0, 200.0
        cx, cy, span = sdgeo.slot_mouth(geo, (bx, by, bw, bh))
        assert bx <= cx <= bx + bw and by <= cy <= by + bh, key
        near = {"left": cx - bx, "right": bx + bw - cx,
                "bottom": cy - by, "top": by + bh - cy}[geo.edge]
        assert near < min(bw, bh) * 0.25, f"{key}: mouth is not near its edge"
        assert span > 0


def test_the_card_is_sized_by_the_hole_it_goes_into():
    """Not by the stage. An 11 mm card in an 11 mm mouth is right at whatever
    scale the board happens to be drawn, and it means a narrow-slot board
    cannot end up with a card visibly too fat for it."""
    geo = sdgeo.PI_SLOTS["pi_zero_2w"]
    brect = (0.0, 0.0, 300.0, 200.0)
    _cx, _cy, span = sdgeo.slot_mouth(geo, brect)
    length, width = sdgeo.card_size(geo, brect, 1.324)
    assert width == pytest.approx(span * 0.92)
    assert length == pytest.approx(width * 1.324)


def test_the_card_starts_in_the_reader_and_ends_in_the_slot():
    geo = sdgeo.PI_SLOTS["pi_zero_2w"]
    brect = (400.0, 20.0, 300.0, 110.0)
    seat = (120.0, 70.0)
    first = sdgeo.handover_frame(0.0, geo, brect, 40.0, seat, 61.0)
    last = sdgeo.handover_frame(1.0, geo, brect, 40.0, seat, 61.0)
    assert (first.cx, first.cy) == pytest.approx(seat)
    assert abs(first.angle - 61.0) < 1e-6, "it leaves along the reader's axis"
    ex, ey = sdgeo.seated_centre(geo, brect, 40.0)
    assert (last.cx, last.cy) == pytest.approx((ex, ey), abs=1.0)
    assert abs(last.angle - sdgeo.entry_angle(geo)) < 1e-6
    assert last.seated == 1.0


def test_the_card_turns_the_short_way_round():
    """From 61° to a slot at 270° the card must turn -151°, not +209°: the long
    way is a card doing a full cartwheel across the stage."""
    mk = dict(along=0.5, mouth=0.2, inset=0.05, face="top",
              retention="friction", card_face_up=True, sprite="x",
              board_box=(0, 0, 1, 1), board_mm=(85.0, 56.0))
    geo = sdgeo.SlotGeometry(edge="top", **mk)
    mid = sdgeo.handover_frame(0.45, geo, (400, 20, 300, 110), 40.0,
                               (120.0, 70.0), 61.0)
    assert -100.0 < mid.angle < 61.0


def test_an_underside_slot_puts_the_card_behind_the_board():
    """Which side the holder is on is a DRAWING decision, not a label: a card
    going into an underside slot must pass behind the board and be hidden by
    it, because that is what the operator will see happen."""
    for key, geo in sdgeo.PI_SLOTS.items():
        f = sdgeo.handover_frame(0.8, geo, (400, 20, 300, 110), 40.0,
                                 (120.0, 70.0), 61.0)
        assert f.behind_board == (geo.face == "underside"), key


def test_the_card_goes_in_and_stays_put():
    """No click, no spring-back, no settle — on any board. The animation once
    had a recoil for push-push slots; the research established that no
    supported model has one, so the recoil is gone rather than merely unused.
    A card that visibly springs invites the operator to push a seated one."""
    for key, geo in sdgeo.PI_SLOTS.items():
        brect = (400, 20, 300, 110)
        ex, ey = sdgeo.seated_centre(geo, brect, 40.0)
        for t in (0.91, 0.95, 1.0):
            f = sdgeo.handover_frame(t, geo, brect, 40.0, (120.0, 70.0), 61.0)
            assert (f.cx, f.cy) == pytest.approx((ex, ey)), f"{key} moves at {t}"


def test_an_underside_board_makes_the_card_turn_over():
    """THE label problem, and the honest answer to it.

    Contacts always face the PCB, so on the four underside boards the card goes
    in label DOWN. The artwork is the label face, so a top-view Pi 4 with that
    sprite sliding into it is a picture of the operator doing it WRONG. The card
    therefore turns over in mid-air, and what enters the board is its contact
    side.
    """
    args = ((400, 20, 300, 110), 40.0, (120.0, 70.0), 61.0)
    pi4 = sdgeo.PI_SLOTS["pi_4b"]
    assert sdgeo.handover_frame(0.30, pi4, *args).showing_back is False
    assert sdgeo.handover_frame(0.62, pi4, *args).showing_back is True
    assert sdgeo.handover_frame(1.00, pi4, *args).showing_back is True
    # edge-on at the halfway point, flat-on either side of it
    mid = sdgeo.handover_frame((sdgeo._FLIP_FROM + sdgeo._FLIP_TO) / 2.0,
                               pi4, *args)
    assert mid.face_scale < 0.05
    assert sdgeo.handover_frame(0.30, pi4, *args).face_scale == 1.0
    assert sdgeo.handover_frame(1.00, pi4, *args).face_scale == 1.0


def test_a_zero_never_turns_over_because_its_holder_is_on_top():
    """The visible difference between the two kinds of board. A Zero's card
    goes in exactly as it left the reader, label up."""
    args = ((400, 20, 300, 110), 40.0, (120.0, 70.0), 61.0)
    zero = sdgeo.PI_SLOTS["pi_zero_2w"]
    for t in (0.0, 0.3, 0.5, 0.8, 1.0):
        f = sdgeo.handover_frame(t, zero, *args)
        assert f.showing_back is False and f.face_scale == 1.0


def test_the_card_back_is_a_card_back_and_not_a_blank_rectangle():
    """Eight gold pads along the leading edge — the thing that makes the
    reverse recognisable as the side that must face the board."""
    body, pads = sdgeo.card_back_shapes(40.0, 30.0)
    assert body == (-20.0, -15.0, 40.0, 30.0)
    assert len(pads) == 8
    for x, y, w, h in pads:
        assert x > 0, "the contacts are at the LEADING edge"
        assert x + w <= 20.0 and abs(y) + h <= 15.0, "a pad is off the card"


def test_the_clip_keeps_the_part_of_the_card_still_outside():
    """The half-plane the card is drawn through must contain the approach side
    of the mouth and exclude the inside of the board — the wrong sign hides
    exactly the half that should be visible."""
    geo = sdgeo.PI_SLOTS["pi_zero_2w"]
    brect = (400.0, 20.0, 300.0, 110.0)
    f = sdgeo.insert_frame(0.5, geo, brect, 40.0)
    mx, my, _span = sdgeo.slot_mouth(geo, brect)
    pts = sdgeo.half_plane(f.clip_point, f.clip_normal, (0, 0, 760, 150))
    xs = pts[0::2]
    assert max(xs) <= mx + 0.01, "the mask must stop at the mouth"
    assert min(xs) < mx


@pytest.mark.parametrize("key", sorted(sdgeo.PI_SLOTS))
def test_the_card_never_leaves_the_stage(key):
    """Kivy does not clip a widget's canvas. A card whose run-up starts below
    the stage is not invisible — it is painted across the body text under it,
    the same trap the connect-Pi step's control points are pinned for. This is
    the test the layout constants were tuned against."""
    geo, lay = _layout(key)
    x0, y0, x1, y1 = sdgeo.path_bounds(geo, lay)
    sx, sy, sw, sh = STAGE
    assert x0 >= sx - 0.5 and y0 >= sy - 0.5, (
        f"{key}: the card runs off the stage at ({x0:.1f}, {y0:.1f})")
    assert x1 <= sx + sw + 0.5 and y1 <= sy + sh + 0.5, (
        f"{key}: the card runs off the stage at ({x1:.1f}, {y1:.1f})")


@pytest.mark.parametrize("key", sorted(sdgeo.PI_SLOTS))
def test_the_board_and_the_reader_both_fit_the_stage(key):
    geo, lay = _layout(key)
    sx, sy, sw, sh = STAGE
    for name, rect in (("pi", lay.pi), ("reader", lay.reader),
                       ("medic", lay.medic)):
        if rect is None:
            continue
        rx, ry, rw, rh = rect
        assert rx >= sx - 1 and ry >= sy - 1, f"{key}: {name} off the stage"
        assert rx + rw <= sx + sw + 1 and ry + rh <= sy + sh + 1, \
            f"{key}: {name} off the stage"


# --------------------------------------------------------------------------- #
# the measured document
# --------------------------------------------------------------------------- #

DOC = """
# Pi microSD slot geometry

| Board | Edge | Along | Mouth width | Inset | Face | Retention | Orientation |
|-------|------|-------|-------------|-------|------|-----------|-------------|
| Raspberry Pi 4 B (`pi_4b`) | bottom | 22% | 15% | 4% | underside | friction | label down |
| Raspberry Pi 5 (`pi_5`) | bottom | 0.19 | 0.14 | 0.045 | underside | push-push, it clicks | label away from you |
| Something else (`pi_999`) | left | 50% | 20% | 5% | top | friction | label up |
"""


def test_the_document_is_read_when_it_lands():
    got = sdgeo.parse_doc(DOC)
    assert got["pi_4b"]["edge"] == "bottom"
    assert got["pi_4b"]["along"] == pytest.approx(0.22)
    assert got["pi_4b"]["mouth"] == pytest.approx(0.15)
    assert got["pi_4b"]["inset"] == pytest.approx(0.04)
    assert got["pi_4b"]["face"] == "underside"
    assert got["pi_4b"]["retention"] == "friction"
    assert got["pi_4b"]["card_face_up"] is False
    assert got["pi_5"]["retention"] == "push_push"
    assert got["pi_5"]["along"] == pytest.approx(0.19)


def test_the_document_replaces_what_it_covers_and_nothing_else():
    merged = sdgeo.apply_doc(sdgeo.PI_SLOTS, DOC)
    assert merged["pi_4b"].along == pytest.approx(0.22)
    assert merged["pi_4b"].edge == "bottom"
    # a model the sample does not mention is left exactly as it was
    assert merged["pi_3a_plus"] == sdgeo.PI_SLOTS["pi_3a_plus"]
    # a board we have never heard of is ignored rather than invented
    assert "pi_999" not in merged


def test_a_cell_it_cannot_read_leaves_the_placeholder_alone():
    """Half-understanding somebody else's table is how a card ends up entering
    the wrong edge while the code reports itself as measured."""
    junk = """
| Board | Edge | Along | Inset |
|---|---|---|---|
| `pi_4b` | diagonal | miles | 4% |
"""
    merged = sdgeo.apply_doc(sdgeo.PI_SLOTS, junk)
    assert merged["pi_4b"].edge == sdgeo.PI_SLOTS["pi_4b"].edge
    assert merged["pi_4b"].along == sdgeo.PI_SLOTS["pi_4b"].along
    assert merged["pi_4b"].inset == pytest.approx(0.04)   # the one good cell


def test_the_real_document_agrees_with_the_table_it_produced():
    """If docs/pi_sd_slot_geometry.md is present, what it states and what is
    baked in here must not have drifted apart. It is written by a separate pass
    and may not have landed in this tree yet."""
    if not os.path.exists(sdgeo.DOC_PATH):
        pytest.skip("the geometry document has not landed in this tree")
    with open(sdgeo.DOC_PATH, encoding="utf-8") as fh:
        stated = sdgeo.parse_doc(fh.read())
    for key, rec in stated.items():
        geo = sdgeo.PI_SLOTS.get(sdgeo.ALIASES.get(key, key))
        if geo is None or "edge" not in rec:
            continue
        assert geo.edge == rec["edge"], f"{key}: the doc says {rec['edge']}"


def test_a_document_that_is_not_there_is_not_an_error():
    """It is written by a separate pass and may simply not exist yet."""
    assert sdgeo.apply_doc(sdgeo.PI_SLOTS, "") == sdgeo.PI_SLOTS
    sdgeo.reload_doc()                        # must not raise either way


# --------------------------------------------------------------------------- #
# the wiring (source inspection — Kivy is not importable here)
# --------------------------------------------------------------------------- #

def test_the_animation_asks_the_geometry_instead_of_hardcoding_one_board():
    body = src(WIDGET)
    assert "pi_sd_geometry" in body
    assert "_SLOT_TOP" not in body, (
        "the Pi Zero's cage fractions are back in the drawing code — that is "
        "the single-board assumption this replaced")


def test_the_picture_and_the_numbers_travel_together():
    """The slot fractions are measured inside one crop, so the animation must
    take the sprite the geometry names — not whatever photo happens to be on
    disk for that key."""
    body = src(WIDGET)
    assert "sdgeo.sprite_path" in body


def test_an_underside_slot_is_drawn_before_the_board_and_a_top_one_after():
    """Draw order IS the difference between 'the card goes under the board' and
    'the card lies on top of it'. Both cases must exist in the drawing code."""
    body = src(WIDGET)
    assert "frame.behind_board" in body
    assert "_draw_card_ghost" in body, (
        "an underside insertion is invisible without the cutaway — the "
        "operator watches the card vanish and nothing happen")


def test_the_step_is_wired_and_told_which_pi():
    screen = src(SCREEN)
    assert '"sd_handover": SdHandoverAnim' in screen
    assert "_PI_ANIMS" in screen and "_pi_key_for_art" in screen


def test_the_operators_own_answer_wins_over_the_usb_guess():
    """A BCM283x reports as a Zero 2 W, a 3A+ and a 3B+ at once, so the USB
    fallback is empty for all three — which is how a Pi Zero came to be drawn
    while a 3A+ was on the bench (operator, 2026-08-06)."""
    seg = func_source(SCREEN, "_pi_key_for_art")
    final = [l for l in seg.splitlines() if l.strip().startswith("return")][-1]
    assert "_pi_key" in final and "_pi_art_key" in final
    assert final.index("_pi_key") < final.index("_pi_art_key"), \
        "the operator's own answer must be consulted first"


def test_the_card_found_ripple_matches_the_board_connected_one():
    """One shared visual language: green rings radiating from where the thing
    is, meaning "the medic can see it now" — the same whether what turned up
    is a board or a card (operator, live on the medic)."""
    body = src(WIDGET)
    i = body.index("def mark_card_found")
    seg = body[i:i + 3200]
    assert "self._found" in seg and "return" in seg, "must be idempotent"
    assert "Animation(burst=1.6" in seg


def test_the_card_found_ripple_actually_finishes():
    """Every ring must reach zero alpha, or the burst leaves a frozen residue.

    Ring i's alpha is (1 - f) * 0.9 where f = min(1, burst - i*0.16). At the old
    burst target of 1.0 only ring 0 faded; rings 1-3 stopped mid-flight and sat
    at 0.14 / 0.29 / 0.43 forever. The operator read that as a hang — "the green
    ring animation ... froze ... it gives the impression the process has
    stalled" (2026-08-08) — and they were right to: a finished animation that
    looks stuck is worse than a slow one, because there is nothing to wait for.

    Derived from the source rather than hardcoded, so changing the stagger or
    the ring count re-checks the maths instead of silently breaking it."""
    import re
    body = src(WIDGET)
    seg = body[body.index("def mark_card_found"):][:3200]
    target = float(re.search(r"Animation\(burst=([\d.]+)", seg).group(1))
    rip = body[body.index("def _ripples"):][:1400]
    count = int(re.search(r"for i in range\((\d+)\)", rip).group(1))
    stagger = float(re.search(r"i \* ([\d.]+)", rip).group(1))
    for i in range(count):
        f = min(1.0, target - i * stagger)
        alpha = (1.0 - f) * 0.9
        assert alpha == 0, (
            f"ring {i} is left at alpha {alpha:.3f} when the burst ends — "
            f"burst must reach {1 + (count - 1) * stagger:.2f} for every ring "
            f"to fade out")
    rip = body[body.index("def _ripples"):]
    assert "Color(0.2, 0.9, 0.4," in rip, "same green, same fade"
    assert "for i in range(4)" in rip, "same four rings"


def test_the_card_found_ripple_says_nothing_about_writing():
    """Writing the card is destructive and stays behind a deliberate press. The
    ripple means "I can see your card" and nothing more — so it carries no
    text at all, which also keeps it out of the translation catalogues."""
    body = src(WIDGET)
    seg = body[body.index("def mark_card_found"):body.index("def _blit", body.index(
        "def mark_card_found"))]
    assert "CoreLabel" not in seg and "_label(" not in seg


# --- the doc and the runtime must AGREE, loudly --------------------------------
# 2026-08-07: the doc override failed in two ways at once, both SILENTLY.
#   * retention: the cell reads "friction — no click"; the parser matched the
#     bare word "click" and set push_push — reading a denial as an affirmation,
#     so every board claimed a click while the doc and every built-in said
#     friction.
#   * along: the doc's column is "`v` (from GPIO edge)", which matched none of
#     the parser's aliases, so it never overrode at all and a stale 0.43 stood
#     against the measured 0.41.
# A parser that misreads quietly and skips quietly is worse than no parser: it
# looks like a single source of truth while being neither.

_MEASURED = {
    "pi_zero_2w": (0.41, "top",       "friction"),
    "pi_3a_plus": (0.50, "underside", "friction"),
    "pi_3b_plus": (0.48, "underside", "friction"),
    "pi_4b":      (0.49, "underside", "friction"),
    "pi_5":       (0.49, "underside", "friction"),
}


def test_runtime_geometry_matches_what_was_actually_measured():
    """These numbers came from underside photographs located against each
    board's own mounting holes. If this fails, either the doc changed or the
    parser broke — and the operator would be shown a card entering the wrong
    place with nothing to warn them."""
    from ui.pi_sd_geometry import geometry_for
    for key, (along, face, retention) in _MEASURED.items():
        g = geometry_for(key)
        assert g is not None, f"no geometry for {key}"
        assert abs(g.along - along) < 0.005, f"{key} along {g.along} != {along}"
        assert g.face == face, f"{key} face {g.face} != {face}"
        assert g.retention == retention, f"{key} retention {g.retention}"


def test_a_denial_of_clicking_is_not_read_as_clicking():
    """Guards the exact misread: the words 'no click' must never produce a
    push-push socket."""
    from ui.pi_sd_geometry import parse_doc
    doc = (
        "| Model | Slot edge | `v` (from GPIO edge) | Face | Retention |\n"
        "|---|---|---|---|---|\n"
        "| `pi_4b` | left short edge | **0.49** | UNDERSIDE | friction — no click |\n"
    )
    got = parse_doc(doc)
    assert got.get("pi_4b", {}).get("retention") == "friction", got
    assert abs(got.get("pi_4b", {}).get("along", 0) - 0.49) < 0.005, got
