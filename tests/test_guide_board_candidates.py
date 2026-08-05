"""The guide must not ask a question it can already answer.

Live 2026-08-06, choosing "Pi + radio" with a RAK4631 plugged in:

  "the rack is very plugged in and medic should have been able to recognise it"

They were right. A fresh read of that board is completely unambiguous —
vendor 239a -> board_key rak4631, a single candidate, no guesswork. Yet the
screen listed the whole ESP32 catalogue, with NO RAK4631 in it, under copy
promising "Node Medic has narrowed it to these". The operator was asked to
identify a board the medic had already identified, and then offered a list that
could not contain the right answer.

THE CAUSE was staleness, not detection. ``_detected`` is written in exactly one
place — the "What are you building?" chooser — and never refreshed. On the Pi
path the radio question comes several screens and a naming step later, by which
time the operator may have plugged the radio in, or swapped it for another. A
snapshot taken before the board arrived says "no boards", and the fallback lists
everything.

Kivy cannot be imported in the suite (process-global stubs cover only the
submodules already in use), so these compile the SHIPPED function and run it
against a stub self — the tests/srcutil idiom used by test_birth_back_swipe.
"""
import sys
import textwrap
import types

from tests.srcutil import func_source

SCREEN = "ui/screens/birth_guide_screen.py"


class _Board:
    def __init__(self, key, name):
        self.key, self.display_name = key, name


RAK = _Board("rak4631", "RAK4631")
V4 = _Board("heltec32_v4", "Heltec LoRa32 v4")


def _candidates(detected, *, ports=("/dev/ttyACM2",), fresh=None,
                raise_detect=False, catalogue=(RAK, V4), on_detect=None,
                monkeypatch=None):
    """Run the real _board_candidates with the modules it imports stubbed.

    sys.modules stubbing (not an __import__ override) because that is what the
    rest of this suite does and what actually intercepts ``from x import y``.
    """
    src = textwrap.dedent(func_source(SCREEN, "_board_candidates"))
    ns = {}
    exec(compile(src, SCREEN, "exec"), ns)

    def _detect_board(_boards, ports_fn=None, **_k):
        if on_detect is not None:
            on_detect()
        if raise_detect:
            raise OSError("board read failed")
        return fresh or {}

    stubs = {
        "ui.board_detect": types.SimpleNamespace(detect_board=_detect_board),
        "ui.hw_factories": types.SimpleNamespace(
            local_board_ports=lambda *a, **k: list(ports)),
        "workflows.rnode_boards": types.SimpleNamespace(
            RNODE_BOARDS={b.key: b for b in catalogue}),
        "ui.birth": types.SimpleNamespace(
            rnode_board_choices=lambda: list(catalogue)),
    }
    for name, mod in stubs.items():
        monkeypatch.setitem(sys.modules, name, mod)
    self = types.SimpleNamespace(_detected=detected)
    return ns["_board_candidates"](self), self


def test_a_connected_board_is_recognised_even_if_the_snapshot_predates_it(monkeypatch):
    """THE bug: the chooser ran before the radio was plugged in."""
    out, _ = _candidates({}, fresh={"boards": [RAK], "port": "/dev/ttyACM2"}, monkeypatch=monkeypatch)
    assert out == [("rak4631", "RAK4631")]


def test_a_swapped_board_is_re_read_rather_than_remembered(monkeypatch):
    """The snapshot names a port that is no longer a work board — the operator
    unplugged one radio and plugged in another between the two screens."""
    out, _ = _candidates({"boards": [V4], "port": "/dev/ttyUSB0"},
                         ports=("/dev/ttyACM2",),
                         fresh={"boards": [RAK], "port": "/dev/ttyACM2"},
                         monkeypatch=monkeypatch)
    assert out == [("rak4631", "RAK4631")]


def test_a_still_valid_snapshot_is_NOT_re_read(monkeypatch):
    """Detection RESETS an ESP32 board, so it must not happen for nothing."""
    calls = []
    out, _ = _candidates({"boards": [V4], "port": "/dev/ttyACM2"},
                         ports=("/dev/ttyACM2",),
                         fresh={"boards": [RAK]},
                         on_detect=lambda: calls.append(1),
                         monkeypatch=monkeypatch)
    assert out == [("heltec32_v4", "Heltec LoRa32 v4")]
    assert not calls, "re-read a snapshot that was still good"


def test_a_failed_read_falls_back_to_everything_not_to_nothing(monkeypatch):
    """Fail OPEN. An empty list strands the operator with no way forward; a
    long list is merely annoying."""
    out, _ = _candidates({}, raise_detect=True, monkeypatch=monkeypatch)
    assert len(out) > 1, "a failed read must not leave the operator stuck"


def test_the_result_is_never_empty(monkeypatch):
    out, _ = _candidates({}, fresh={"boards": []}, monkeypatch=monkeypatch)
    assert out, "the picker must always offer something"


def test_a_fresh_read_is_remembered_so_it_is_not_repeated(monkeypatch):
    _, self = _candidates({}, fresh={"boards": [RAK], "port": "/dev/ttyACM2"}, monkeypatch=monkeypatch)
    assert [b.key for b in self._detected.get("boards", [])] == ["rak4631"]
