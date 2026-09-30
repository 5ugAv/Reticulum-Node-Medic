"""Front-page poster tap zones — pure geometry."""

from ui.home_zones import zone_at, CARD_ORDER, CARDS_TOP


def test_five_cards_left_to_right():
    y = 0.9                                   # inside the card row
    hits = [zone_at((i + 0.5) / 5, y) for i in range(5)]
    assert hits == ["vitals", "scan", "birth", "triage", "chat"]
    assert hits == CARD_ORDER


def test_card_row_edges_full_bleed():
    # the 720x1280 cut runs the cards to the screen edges
    assert zone_at(0.02, CARDS_TOP + 0.01) == "vitals"
    assert zone_at(0.98, 0.99) == "chat"      # the fifth card is CHAT since 2026-08-07
    assert zone_at(0.5, CARDS_TOP - 0.02) is None   # just above the cards


def test_lora_trunk_node_opens_the_credits_easter_egg():
    """The egg moved with the artwork (2026-09-28): the leaf that carried it was
    retired, and the credits now live on the LORA trunk node — the filled disc
    the whole mesh grows out of. The network's origin opening the credits for
    the people who built the network."""
    assert zone_at(0.499, 0.170) == "credits"  # dead centre of the disc
    assert zone_at(0.46, 0.155) == "credits"   # inside the circle
    assert zone_at(0.50, 0.47) is None         # the INTERNET marker — inert
    assert zone_at(0.15, 0.17) is None         # off to the side, blank mesh


def test_wifi_marker_opens_wifi_settings():
    assert zone_at(0.545, 0.281) == "wifi"     # on the WI-FI ring + label
    assert zone_at(0.499, 0.170) == "credits"  # the LORA disc above it
    assert zone_at(0.50, 0.345) is None        # BLUETOOTH — painted, inert
    assert zone_at(0.50, 0.469) is None        # INTERNET — painted, inert


def test_out_of_image_taps_are_none():
    assert zone_at(-0.1, 0.5) is None
    assert zone_at(0.5, 1.2) is None


# --- the zones are a claim about PAINTED WORDS (task #22) --------------------
# The home screen is a tap-map over assets/ui/front_page.png. CARD_ORDER is not
# a menu the tool renders — it is an assertion about text already printed on the
# artwork. Read off the image 2026-08-07, the five cards say:
#
#   VITALS / CHECK NODE HEALTH        SCAN / VIEW MAPS & TOPOLOGY
#   BIRTH / BUILD NEW NODES           TRIAGE / SITE ASSESSMENT & ANTENNA PLACEMENT
#   PROBE / DIAGNOSE & REPAIR
#
# #22 wants PROBE off the front page to make room for CHAT (#23). That cannot be
# a code change: re-pointing the fifth entry would leave a card labelled PROBE,
# illustrated with a toolbox, opening a chat screen. New art comes first.

def test_the_tap_zones_match_the_words_on_the_poster():
    from ui.home_zones import CARD_ORDER, POSTER_CARD_LABELS
    assert len(CARD_ORDER) == len(POSTER_CARD_LABELS)
    # Since the 2026-09-13 repaint the painted word and the screen key are
    # DIFFERENT strings (SCAN screen, MAPS card) — the law survives as an
    # explicit mapping: change either side alone and this still fails.
    from ui.home_zones import POSTER_WORD_FOR
    assert [POSTER_WORD_FOR[c] for c in CARD_ORDER] == POSTER_CARD_LABELS, (
        "a tap zone no longer matches the word painted on that card — if the "
        "artwork changed, update POSTER_CARD_LABELS and POSTER_WORD_FOR in "
        "the same commit")


def test_the_poster_is_still_the_size_the_zones_assume():
    """Fractions survive scaling, but the card row's y-cut (0.79) was measured
    on the 720x1280 art. A re-crop moves it."""
    import os
    from PIL import Image                       # pillow is a test-only dep here
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "ui", "front_page.png")
    if not os.path.exists(path):                # gitignored on CI
        return
    assert Image.open(path).size == (720, 1280)


# --- CHAT replaced PROBE on the front row (2026-08-07) ----------------------
# The operator supplied artwork in the poster's own style; it was composited
# into the fifth slot and the zone followed it in the SAME commit. That is the
# only order in which this change is allowed to happen.

def test_the_fifth_card_is_chat():
    from ui.home_zones import CARD_ORDER
    assert CARD_ORDER[-1] == "chat"
    assert "probe" not in CARD_ORDER


def test_probe_is_still_reachable_somewhere():
    """Removing PROBE from the poster must not orphan it — Self Diagnose lives
    under it, and it is the medic's own health check."""
    from ui.widgets.sidebar import MODES as ITEMS
    assert any(key == "probe" for key, _label in ITEMS), (
        "PROBE has no door left anywhere")


def test_chat_opens_a_screen_that_actually_exists():
    """The whole point of not doing this earlier: a card that looks alive and
    does nothing. switch_mode silently ignores an unknown screen name, so a
    wrong mapping here fails INVISIBLY."""
    from ui.home_zones import screen_for
    assert screen_for("chat") == "chat"
    import os
    assert os.path.exists("ui/screens/chat_screen.py")


def test_every_card_maps_to_a_real_screen_name():
    """Guard for the next card that gets renamed on the art."""
    import os
    from ui.home_zones import CARD_ORDER, screen_for
    known = {"vitals", "scan", "birth", "triage", "probe", "comms", "mitosis", "chat"}
    for zone in CARD_ORDER:
        target = screen_for(zone)
        assert target in known, f"{zone} -> {target}, which no screen answers"


def test_unmapped_zones_pass_straight_through():
    from ui.home_zones import screen_for
    assert screen_for("vitals") == "vitals"
    assert screen_for("credits") == "credits"


def test_chat_keeps_the_phone_handoff_in_plain_words():
    """Both messengers stay: the medic's own chat AND the phone's. A quick look
    on 2026-09-30 read the corner 'Phone apps' button as 'the APKs are gone'."""
    src = open("ui/screens/chat_screen.py").read()
    assert 'tr("Put Columba or Sideband on a phone  →")' in src
    assert src.count("self._open_phone_apps()") >= 2          # corner button + full-width row
    app = open("ui/app.py").read()
    assert 'open_phone_apps=lambda: self.switch_mode("comms")' in app
    assert 'Screen(name="comms")' in app                        # the APK page itself still exists
