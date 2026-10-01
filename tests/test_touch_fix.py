"""Two fingers reach the app (ui/touch_fix.py, 2026-10-02)."""
import collections
import sys
import types


def _fake_kivy(monkeypatch):
    prov = types.SimpleNamespace(q=collections.deque())
    mod = types.ModuleType("kivy.core.window.window_sdl2"); mod.SDL2MotionEventProvider = prov
    monkeypatch.setitem(sys.modules, "kivy", types.ModuleType("kivy"))
    monkeypatch.setitem(sys.modules, "kivy.core", types.ModuleType("kivy.core"))
    monkeypatch.setitem(sys.modules, "kivy.core.window", types.ModuleType("kivy.core.window"))
    monkeypatch.setitem(sys.modules, "kivy.core.window.window_sdl2", mod)
    return prov


def test_finger_events_go_to_the_provider_and_the_rest_pass_through(monkeypatch):
    from ui import touch_fix
    monkeypatch.setattr(touch_fix, "_installed", False); monkeypatch.setattr(touch_fix, "_seen", 0)
    prov = _fake_kivy(monkeypatch)
    events = [("mousemotion", 1, 2), ("fingerdown", 7, 0.1, 0.2, 1.0), ("fingermotion", 7, 0.2, 0.2, 1.0),
              ("fingerdown", 8, 0.5, 0.5, 1.0), ("quit",), False]
    class _Cython:                                  # attributes read-only, like the real one
        __slots__ = ("other",)
        def poll(self): return events.pop(0)
        def resize_window(self, w, h): return ("resized", w, h)
    win = _Cython()
    window = types.SimpleNamespace(_win=win)
    logs = []
    assert touch_fix.install(window, log=logs.append) is True
    assert window._win is not win and window._win.resize_window(1, 2) == ("resized", 1, 2)
    got = [window._win.poll() for _ in range(6)]
    assert got == [("mousemotion", 1, 2), None, None, None, ("quit",), False]
    assert [e[0] for e in prov.q] == ["fingerdown", "fingermotion", "fingerdown"][::-1]
    assert prov.q.pop()[0] == "fingerdown"                   # FIFO, as Kivy's provider pops
    assert touch_fix._seen == 3 and len(logs) == 1
    assert touch_fix.install(window) is True                 # idempotent
    window._win.other = 5; assert win.other == 5             # writes reach the real one


def test_without_an_sdl_window_it_declines(monkeypatch):
    from ui import touch_fix
    monkeypatch.setattr(touch_fix, "_installed", False)
    _fake_kivy(monkeypatch)
    assert touch_fix.install(types.SimpleNamespace(_win=None)) is False


def test_the_launcher_stops_sdl_making_a_mouse_from_a_finger():
    sh = open("scripts/start_ui.sh").read()
    assert "export SDL_TOUCH_MOUSE_EVENTS=0" in sh
    app = open("ui/app.py").read()
    assert "touch_fix.install(Window" in app


def test_node_detail_echo_line_grows():
    src = open("ui/screens/node_detail_screen.py").read()
    i = src.index("a relay repeating its last announce")
    assert "_para(" in src[i - 200:i]
