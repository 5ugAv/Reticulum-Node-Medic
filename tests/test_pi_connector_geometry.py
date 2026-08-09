"""The picture of the Pi must be the Pi in the operator's hand, and the sockets
marked on it must be that board's own.

Live, 2026-08-09, with a Pi 3 A+ on the bench: the "Connect the Pi" step drew a
Pi Zero 2 W and ringed the Zero's two micro-USB shells, under wording that
correctly described the 3A+'s full-size USB-A. The ring positions were
fractions measured on the Zero sprite, so handing the animation another model
would have moved the board and left the rings pointing at bare PCB — and rather
than fix that, it kept showing the Zero.

The rule these tests hold: ART AND GEOMETRY COME FROM THE SAME BOARD, ALWAYS.
A board we have not measured gets its own true picture with nothing marked.
"""
import re

from tests.srcutil import func_source
from ui import pi_connector_geometry as geo

ANIM = "ui/widgets/birth_anims.py"


def test_the_boards_we_have_measured():
    assert set(geo.MEASURED) == {"pi_zero_2w", "pi_3a_plus"}


def test_every_entry_names_the_sprite_it_was_measured_on():
    """These fractions describe a DRAWING, not a product. Replace the art and
    they are wrong, with nothing to say so — unless the art is named."""
    for key, s in geo.MEASURED.items():
        assert s.art.endswith(".png"), key
        assert s.key == key


def test_fractions_are_inside_the_picture():
    for key, s in geo.MEASURED.items():
        for name, pt in (("data", s.data), ("power", s.power)):
            if pt is None:
                continue
            assert all(0.0 < v < 1.0 for v in pt), (key, name, pt)


def test_the_zero_keeps_the_numbers_the_animation_already_used():
    """Confirmed against the sprite rather than assumed — the inner micro-USB
    (nearer the mini-HDMI) is data, the outer is PWR IN."""
    z = geo.sockets_for("pi_zero_2w")
    assert z.approach == "bottom"
    assert z.data[0] < z.power[0], "data is the INNER socket"
    assert abs(z.data[1] - z.power[1]) < 0.01, "both on the same bottom edge"


def test_the_3aplus_data_socket_is_on_a_different_edge():
    """THE fault this module exists for. A 3A+ takes data through the full-size
    USB-A on its RIGHT edge and power through a micro-USB underneath — a
    different socket, on a different edge, entered from a different direction.
    Nothing about the Zero's layout transfers."""
    a = geo.sockets_for("pi_3a_plus")
    assert a.approach == "right"
    assert a.data[0] > 0.7, "data socket lives on the right edge"
    assert a.power[1] > 0.85, "power socket is along the bottom"
    assert abs(a.data[1] - a.power[1]) > 0.3, "not the same edge as the Zero's"


def test_an_unmeasured_board_is_not_guessed_at():
    for key in ("pi_4b", "pi_5", "pi_3b_plus", "", "nonsense"):
        assert geo.sockets_for(key) is None
        assert not geo.is_measured(key)


# --- the pairing rule, in the animation ------------------------------------

def test_the_animation_takes_geometry_from_the_same_board_as_the_art():
    src = func_source(ANIM, "__init__", cls="ConnectPiAnim")
    assert "sockets_for(pi_key)" in src, "the operator's board, not a default"
    # the Zero's numbers appear ONLY on the branch where the Zero is drawn
    zero = src[src.index("if not self._pi_png:"):]
    assert 'sockets_for("pi_zero_2w")' in zero
    assert 'sockets_for("pi_zero_2w")' not in src[:src.index("if not self._pi_png:")]


def test_nothing_is_marked_on_a_board_we_have_not_measured():
    """No ring, no NO-ENTRY, no DATA label. The per-model wording in
    ui.pi_connectors carries the answer instead — honest about what is known."""
    src = func_source(ANIM, "_draw", cls="ConnectPiAnim")
    assert "geo is not None" in src, "the markers must be conditional"
    assert "gdx = geo.data[0] if geo else" in src


def test_a_side_entry_board_gets_its_own_scene():
    """A socket on the right edge is entered sideways. The bottom-entry scene
    is built around a plug rising into the lower edge, and bending its numbers
    to fit would draw a plug going through the board."""
    src = func_source(ANIM, "_draw", cls="ConnectPiAnim")
    assert 'approach == "right"' in src and "_draw_side_entry" in src
    side = func_source(ANIM, "_draw_side_entry", cls="ConnectPiAnim")
    assert "Rotate(angle=-90" in side, "the plug has to point the way it travels"


def test_the_labels_sit_off_the_board():
    """Green text on green silkscreen, over the very detail being pointed at
    (offline render, 2026-08-09)."""
    side = func_source(ANIM, "_draw_side_entry", cls="ConnectPiAnim")
    assert "pyy + ph + dp(4)" in side, "DATA above the board"
    assert "pyy - dp(20)" in side, "PWR IN below it"
