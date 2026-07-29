"""On-screen keyboard pure logic — the modal parent-walk must TERMINATE.

Kivy's Window.parent is the Window ITSELF, so walking ``w = w.parent`` from a
field that is NOT inside a ModalView reaches the Window and — without the
self-parent guard — spins forever with the GIL held: the 2026-07-30 BIRTH
"Name this node" freeze (main thread pegged, whole UI dead, SIGTERM ignored).

The widget tree here is plain stub objects; Kivy modules are stubbed before
import, same pattern as test_scan_lines."""

import sys
import types


def _install_kivy_stubs():
    class _Dummy:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _Dummy()

    def _module(name):
        m = types.ModuleType(name)
        m.__getattr__ = lambda attr: _Dummy
        return m

    for name in (
        "kivy", "kivy.app", "kivy.clock", "kivy.core", "kivy.core.window",
        "kivy.graphics", "kivy.metrics", "kivy.uix", "kivy.uix.boxlayout",
        "kivy.uix.button", "kivy.uix.modalview",
    ):
        sys.modules.setdefault(name, _module(name))


_install_kivy_stubs()

from ui.onscreen_keyboard import OnScreenKeyboard  # noqa: E402


class _Node:
    """Featherweight widget stand-in with an explicit parent link."""
    def __init__(self, parent=None):
        self.parent = parent


def _walk(target):
    """Run the real parent-walk with a minimal fake self. Returns normally only
    if the walk terminates (regression = this call never returns and the test
    times out loudly)."""
    fake_self = _Node()          # keyboard stand-in: .parent is None
    OnScreenKeyboard._raise_above_modal(fake_self, target)


def test_walk_terminates_when_root_is_its_own_parent():
    # Kivy reality: root widget chain ends at Window, and Window.parent is
    # Window. A field NOT in a modal must terminate the walk at that self-loop.
    window = _Node()
    window.parent = window                     # the Kivy self-parent quirk
    root = _Node(parent=window)
    field = _Node(parent=_Node(parent=root))   # field -> layout -> root -> window
    _walk(field)                               # returns = fixed; hangs = broken


def test_walk_terminates_on_orphan_field():
    _walk(_Node())                             # parent None -> immediate exit
