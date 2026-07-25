"""VITALS 'quiet' partition — the pure helper that splits recently-heard nodes
from ones that have gone quiet. The screen WIDGET is Kivy (not exercised in CI),
so we stub the Kivy modules vitals_screen imports before importing it; the helper
itself touches no Kivy."""

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
        "kivy", "kivy.clock", "kivy.core", "kivy.core.image", "kivy.core.window",
        "kivy.graphics", "kivy.metrics", "kivy.app", "kivy.uix",
        "kivy.uix.boxlayout", "kivy.uix.button", "kivy.uix.floatlayout",
        "kivy.uix.gridlayout", "kivy.uix.label", "kivy.uix.scrollview",
        "kivy.uix.textinput", "kivy.uix.widget", "kivy.uix.popup",
        "kivy.properties",
    ):
        sys.modules.setdefault(name, _module(name))


_install_kivy_stubs()

from ui.screens.vitals_screen import partition_quiet  # noqa: E402


def test_partition_splits_and_preserves_order():
    rows = [
        {"name": "A"}, {"name": "B", "quiet": True},
        {"name": "C"}, {"name": "D", "quiet": True},
    ]
    active, quiet = partition_quiet(rows)
    assert [n["name"] for n in active] == ["A", "C"]     # order kept
    assert [n["name"] for n in quiet] == ["B", "D"]


def test_missing_flag_counts_as_active():
    active, quiet = partition_quiet([{"name": "X"}])     # no 'quiet' key
    assert [n["name"] for n in active] == ["X"]
    assert quiet == []


def test_all_quiet_or_all_active():
    assert partition_quiet([{"quiet": True}, {"quiet": True}])[0] == []
    assert partition_quiet([{"quiet": False}])[1] == []
