"""Walk the guided birth and photograph every step, without touching hardware.

The counterpart to preview_sd_handover: that one draws animation geometry with
PIL, this one renders the REAL screens. Source tests cannot see a NameError in
a canvas call, a hint that overflows the body text beneath it, a step with no
button on it, or a yellow warning that contradicts the instruction above it —
and all four of those shipped in the week of 2026-08-04. Every one was obvious
in a picture.

    python3 scripts/preview_birth_walk.py [out_dir]

RUN IT ON THE MEDIC (``DISPLAY=:0``). Kivy needs a compiled SDL2 window
provider and a display; the Mac's source install has neither, so it dies at
import. The medic has both, and a second Kivy window over the running UI is
harmless — the screenshots come from this app's own framebuffer, not the panel.

Two things it learned the hard way, both worth keeping:

  * REGISTER THE FONT. main.py calls configure_fonts() before any screen is
    built; skip it and every arrow renders as tofu, so the shots libel buttons
    that are fine on the device.
  * CANCEL THE PENDING SELF-ADVANCE before each render. A passed gate schedules
    _next() 1.6 s later, and walking the steps by hand let those fire into
    whatever was on screen by then — step 8 came back in step 6's file. A
    harness that mislabels its evidence is worse than no harness.

Hardware polls are neutered rather than stubbed to succeed: what wants looking
at is the screen the operator sits in front of WHILE the medic is still
searching, which is where they spend the time.
"""

import os
import sys

os.environ.setdefault("KIVY_NO_ARGS", "1")
os.environ.setdefault("KIVY_NO_CONSOLELOG", "1")

# The repo this script lives in — NOT a hardcoded path. It has to run on the
# medic (the only machine with a working Kivy window) and be editable on the
# Mac, and the first committed version carried the Mac's path into both.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "shots")
os.makedirs(OUT, exist_ok=True)

from kivy.config import Config                                    # noqa: E402
Config.set("graphics", "width", "1280")
Config.set("graphics", "height", "720")
Config.set("graphics", "resizable", "0")
Config.set("input", "mouse", "mouse,disable_multitouch")

from kivy.app import App                                          # noqa: E402
from kivy.clock import Clock                                      # noqa: E402
from kivy.core.window import Window                               # noqa: E402
from kivy.uix.boxlayout import BoxLayout                          # noqa: E402

# main.py registers the display font before any screen is built; without it
# every arrow glyph renders as tofu and the shots lie about the buttons.
from ui.fonts import configure_fonts                              # noqa: E402
configure_fonts()

import ui.screens.birth_guide_screen as bgs                       # noqa: E402
from ui.birth_guide_flow import guide_steps                       # noqa: E402

PATH = os.environ.get("WALK_PATH", "pi")
PI_KEY = os.environ.get("WALK_PI", "pi_3a_plus")
BOARD = os.environ.get("WALK_BOARD", "heltec32_v4")

# Every hardware probe answers "nothing here" — the honest state while the
# medic is still looking, and the state the operator spends most time in.
bgs.BirthGuideScreen._start_board_poll = lambda self, *a, **k: None
bgs.BirthGuideScreen._start_absence_poll = lambda self, *a, **k: None
bgs.BirthGuideScreen._start_card_poll = lambda self, *a, **k: None
bgs.BirthGuideScreen._start_card_gone_poll = lambda self, *a, **k: None
bgs.BirthGuideScreen._start_pi_poll = lambda self, *a, **k: None
bgs.BirthGuideScreen._start_node_poll = lambda self, *a, **k: None
bgs.BirthGuideScreen._expect_board_absence = lambda self, *a, **k: None


class Walk(App):
    def build(self):
        self.root_box = BoxLayout()
        self.guide = bgs.BirthGuideScreen(on_complete=lambda *_a: None,
                                          on_navigate=lambda *_a: None)
        self.root_box.add_widget(self.guide)
        return self.root_box

    def on_start(self):
        g = self.guide
        g._path = PATH
        g._node_name = "TESTNODE"
        g._pi_key = PI_KEY
        g._pi_art_key = PI_KEY
        g._board_key = BOARD
        g._pair_checked = True
        self.steps = guide_steps(PATH, PI_KEY)
        self.n = 0
        # gate states worth seeing: radio verified (so gate 2 passes), node not
        # yet found (so gate 7 shows its waiting message)
        g._radio_verified = True
        g._node_addr = ""
        g._node_looking = True
        Clock.schedule_once(self._shoot, 0.6)

    def _shoot(self, _dt):
        g = self.guide
        if self.n >= len(self.steps):
            self._extra()
            return
        s = self.steps[self.n]
        g._i = self.n
        g._gate_warning = ""
        # CANCEL ANY PENDING SELF-ADVANCE. A passed gate schedules _next() 1.6 s
        # later; walking the steps by hand meant those fired into whatever step
        # was on screen by then, and file 06 came back holding step 8. Bumping
        # the token is exactly what a manual tap does.
        g._advance_token = getattr(g, "_advance_token", 0) + 1
        g._nav_token = getattr(g, "_nav_token", 0) + 1
        try:
            g._render_step()
        except Exception as e:                                     # noqa: BLE001
            print(f"!! step {self.n + 1} RAISED {type(e).__name__}: {e}")
        title = s.get("title", "?")[:40].replace("/", "-").replace(" ", "_")
        # LET THE LAYOUT SETTLE, THEN SHOOT, THEN MOVE ON. Overlapping the
        # capture with the next render put step 8 in step 6's file the first
        # time — a harness that mislabels its evidence is worse than none.
        Clock.schedule_once(
            lambda _d, i=self.n, t=title: self._save(f"{i + 1:02d}_{t}"), 1.2)
        self.n += 1
        Clock.schedule_once(self._shoot, 2.4)

    def _extra(self):
        """The two states a step-walk never reaches: a failed build, and done."""
        g = self.guide
        life = next((i for i, s in enumerate(self.steps)
                     if s.get("gate") == "node_online"), None)
        if life is not None:
            g._resume_at = life + 1
            g.resume({"radio_verified": True, "build_failed": True})
            Clock.schedule_once(lambda _d: self._save("90_after_failed_build"), 0.6)
        Clock.schedule_once(lambda _d: self._done_screen(), 1.4)

    def _done_screen(self):
        try:
            self.guide._render_done()
        except Exception as e:                                     # noqa: BLE001
            print(f"!! _render_done RAISED {type(e).__name__}: {e}")
        Clock.schedule_once(lambda _d: self._save("99_done"), 0.5)
        Clock.schedule_once(lambda _d: self.stop(), 1.2)

    def _save(self, name):
        Window.screenshot(name=os.path.join(OUT, f"{name}.png"))
        print(f"   shot {name}")


if __name__ == "__main__":
    Walk().run()
