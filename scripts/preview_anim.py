"""Photograph ONE animation at several points in its loop, on the medic.

preview_birth_walk photographs whole screens, one frame each — which is the
right tool for wording, spacing and buttons, and the wrong one for a scene
that only makes sense as a SEQUENCE. The four-beat setup-Wi-Fi animation
(2026-09-09) could not be judged from a single still: the frame it happened to
catch showed a link at 12% opacity and told you nothing about the three beats
either side.

    WALK_ANIM=ProvisionAnim python3 scripts/preview_anim.py [out_dir]

Writes one PNG per sampled phase, named by phase, plus a contact sheet if PIL
is present. RUN IT ON THE MEDIC — Kivy needs a real window provider (see
preview_birth_walk's own note).

    WALK_ANIM    class name in ui.widgets.birth_anims        (ProvisionAnim)
    WALK_BOARD   board_key for animations that take one      (heltec32_v4)
    WALK_PI      pi_key for the Pi animations                (pi_3a_plus)
    WALK_FRAMES  how many phases to sample across the loop   (8)
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "anim_shots")
os.makedirs(OUT, exist_ok=True)

from kivy.config import Config                                    # noqa: E402
Config.set("graphics", "width", "900")
Config.set("graphics", "height", "420")
Config.set("graphics", "resizable", "0")

from kivy.app import App                                          # noqa: E402
from kivy.clock import Clock                                      # noqa: E402
from kivy.core.window import Window                               # noqa: E402
from kivy.uix.boxlayout import BoxLayout                          # noqa: E402

from ui.fonts import configure_fonts                              # noqa: E402
configure_fonts()

import ui.widgets.birth_anims as anims                            # noqa: E402

NAME = os.environ.get("WALK_ANIM", "ProvisionAnim")
BOARD = os.environ.get("WALK_BOARD", "heltec32_v4")
PI_KEY = os.environ.get("WALK_PI", "pi_3a_plus")
FRAMES = int(os.environ.get("WALK_FRAMES", "8"))


def _build():
    cls = getattr(anims, NAME)
    for kw in ({"board_key": BOARD}, {"pi_key": PI_KEY}, {}):
        try:
            return cls(**kw)
        except TypeError:
            continue
    raise SystemExit(f"cannot construct {NAME}")


class Preview(App):
    def build(self):
        root = BoxLayout()
        self.anim = _build()
        root.add_widget(self.anim)
        return root

    def on_start(self):
        # The loop is DRIVEN BY HAND here: start() ticks it on a clock, and a
        # screenshot taken against a moving phase cannot be labelled with the
        # phase it shows.
        self.anim.stop()
        self.n = 0
        Clock.schedule_once(self._shoot, 0.6)

    def _shoot(self, _dt):
        if self.n >= FRAMES:
            Clock.schedule_once(lambda _d: self.stop(), 0.3)
            return
        ph = self.n / float(FRAMES)
        self.anim.phase = ph
        name = os.path.join(OUT, f"{NAME}_{int(ph * 1000):04d}")
        Clock.schedule_once(lambda _d, p=name: Window.screenshot(name=p + ".png"),
                            0.25)
        print(f"   phase {ph:.3f}")
        self.n += 1
        Clock.schedule_once(self._shoot, 0.6)


if __name__ == "__main__":
    Preview().run()
