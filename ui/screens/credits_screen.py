"""The red cross's secret — credits and the why of it all.

Tapping the cross on the front page lands here: thanks to the people who made
the tool possible, and a few words on what Reticulum gives communities. Tap
anywhere to return to the front page; the five mode buttons along the bottom
jump straight into the tool (mirroring the poster's card row).

Edit CREDITS and SPIEL freely — they're plain data.
"""

from __future__ import annotations

import os
import tempfile

from kivy.metrics import dp
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.image import Image
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView

from ui import theme
from ui.i18n import tr  # i18n: wrapped — credits + spiel (names stay verbatim)

#: Support the developer — a scannable QR of the ETH address (generated offline
#: from ETH_ADDR into assets/ui/donate_eth_qr.png) plus the address in text so it
#: can be read/verified by hand. The QR keeps its white quiet-zone (needed to scan).
ETH_ADDR = "0x93D7c938A85B1AB74950CC9eA0030DfB52bFC42E"
DONATE_QR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "donate_eth_qr.png"))

#: (role, name) — shown in order. Edit to taste.
#: Ordered as the LINEAGE runs, so the thanks read as a chain rather than a
#: list: Reticulum, the C++ port that let it fit on a microcontroller, the
#: firmware forks built on that, then this tool. Everything from Qvist down to
#: GrayHatGuy is GPL-3.0 and we are downstream of all of it.
CREDITS = [
    ("Reticulum & RNode", "Mark Qvist"),
    ("microReticulum — Reticulum in C++, small enough for a microcontroller",
     "Chris Attermann"),
    ("RNode Firmware CE", "Liberated Systems & contributors"),
    ("RTNode for Heltec V4 — the base our fork grew from", "jrl290"),
    ("RTNode-2400 firmware — github.com/GrayHatGuy", "GrayHatGuy"),
    ("Concept, build, field testing & front page", "5ugAv"),
    # Maps: the basemap moved from Carto to Esri on 2026-08-27 (Carto began
    # watermarking keyless tiles); search is OpenStreetMap's Nominatim. The
    # credit must name what the code fetches — tests/test_credits_truth.py
    # holds it to ui/map_download.py.
    ("Maps", "Esri World Street Map & OpenStreetMap contributors"),
    ("Terrain", "Tilezen & AWS Open Data (SRTM)"),
    ("And", "every neighbour who puts a node on a roof"),
]

SPIEL = (
    "Reticulum lets communities build their own communications - networks "
    "that need no towers, no subscriptions, no permission, and no internet. "
    "Off-grid, encrypted, and owned by the people who run the nodes.\n\n"
    "When the weather takes the phone lines, when the power's out, when "
    "you're simply out of range - the mesh keeps talking. Every node "
    "someone adds makes it stronger for everyone else.\n\n"
    "This tool exists to make that easy: to help anyone birth a node, place "
    "it well, keep it healthy, and grow the mesh.\n\n"
    "Think Globally, act Locally."
)


_MESH_PICTURE_SRC = os.path.normpath(os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    os.pardir, "assets", "ui", "anim", "node_medic_cable.png"))


#: Where the rendered tile is written. /tmp on purpose: it is regenerated in
#: 0.15 s after a reboot, and nothing about it deserves a place in the records.
_MESH_PICTURE_OUT = os.path.join(tempfile.gettempdir(), "nodemedic-mesh-picture.png")


def _mesh_picture():
    """(Image widget, caption) for the block-art tile, or (None, None).

    The medic's own artwork, quantised to the tool's 16-step green and drawn
    as blocks; the caption carries the size ui.blockart measured. The green
    ramp rather than DawnBringer's palette on purpose: thirteen revisions of
    poster work settled on ONE green, and a full-colour tile here would be the
    only multicolour thing on the glass. DB16 is a one-word switch if the
    operator wants the post's own look.

    RENDERED TO A FILE AND LOADED BY PATH, never handed to Kivy as bytes. The
    first cut built the texture from a BytesIO with kivy.core.image, and that
    path SEGFAULTED on the medic's own Python under the offscreen harness — a
    segfault is not an Exception, the guard below cannot catch it, and this
    screen is built at app start. Image(source=path) is the loader every board
    photo and the poster already go through; nothing here is a new road.
    """
    try:
        from PIL import Image as PILImage
        from ui import blockart
        if not os.path.exists(_MESH_PICTURE_SRC):
            return None, None
        src = PILImage.open(_MESH_PICTURE_SRC).convert("RGBA")
        ground = tuple(int(round(v * 255)) for v in
                       theme.hex_to_rgba(theme.COLORS["background"])[:3])
        flat = PILImage.new("RGBA", src.size, ground + (255,))
        flat.alpha_composite(src)
        grid = blockart.quantise(flat.convert("RGB"), blockart.DEFAULT_COLS,
                                 blockart.PHOSPHOR16, by_luma=True)
        nbytes = blockart.payload_bytes(grid)
        npk = blockart.packets(nbytes)
        blockart.render(grid, blockart.PHOSPHOR16, cell=8, gap=2,
                        ground=ground).save(_MESH_PICTURE_OUT, format="PNG")
        widget = Image(source=_MESH_PICTURE_OUT, size_hint_y=None, height=dp(260),
                       allow_stretch=True, keep_ratio=True)
        caption = tr("This picture is {n} bytes — {p} LoRa packets. Four bits a "
                     "cell, {c} across: a nod to how pictures may travel the "
                     "mesh.").format(n=f"{nbytes:,}", p=npk, c=blockart.DEFAULT_COLS)
        return widget, caption
    except Exception:                                              # noqa: BLE001
        return None, None


class CreditsScreen(BoxLayout):
    def __init__(self, on_select=None, on_back=None, **kwargs):
        kwargs.setdefault("orientation", "vertical")
        super().__init__(**kwargs)
        self._on_select = on_select
        self._on_back = on_back
        self.padding = [dp(20), dp(24), dp(20), dp(8)]
        self.spacing = dp(8)

        title = Label(text=tr("With thanks"), bold=True, font_size="26sp",
                      size_hint_y=None, height=dp(44),
                      color=theme.hex_to_rgba(theme.COLORS["red"]))
        self.add_widget(title)

        scroll = ScrollView()
        body = BoxLayout(orientation="vertical", size_hint_y=None,
                         spacing=dp(4))
        body.bind(minimum_height=body.setter("height"))
        for role, name in CREDITS:
            # tr() falls straight through for the entries that are pure proper
            # nouns — only the prose roles/lines carry catalog keys.
            row = Label(text=f"[color=9e9e9e]{tr(role)}[/color]\n[b]{tr(name)}[/b]",
                        markup=True, halign="center", valign="middle",
                        size_hint_y=None, height=dp(52),
                        color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            row.bind(size=lambda i, v: setattr(i, "text_size", v))
            body.add_widget(row)
        spiel = Label(text=tr(SPIEL), halign="center", valign="top",
                      font_size="15sp", size_hint_y=None,
                      color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        spiel.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
        spiel.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1] + dp(16)))
        body.add_widget(spiel)

        # --- a picture that fits in a few mesh packets ------------------------
        # Homage (operator, 2026-09-29) to a technique from the Reticulum
        # community: 48 columns, 16 colours, rounded blocks — a picture small
        # enough to send over LoRa. COMPUTED HERE, not pre-baked: the caption's
        # byte and packet count come from ui.blockart on the same image, so the
        # screen says what it measured rather than what somebody once claimed.
        # Best-effort: no PIL, no asset, no caption — never a broken credits
        # page over an ornament.
        pic, caption = _mesh_picture()
        if pic is not None:
            body.add_widget(pic)
            cap = Label(text=caption, halign="center", valign="top",
                        font_size="13sp", size_hint_y=None,
                        color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
            cap.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
            cap.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1] + dp(12)))
            body.add_widget(cap)

        # --- Support this work: ETH address + scannable QR ---
        sup_title = Label(text=tr("Support this work"), bold=True, font_size="18sp",
                          size_hint_y=None, height=dp(40),
                          color=theme.hex_to_rgba(theme.COLORS["red"]))
        body.add_widget(sup_title)
        sup_line = Label(
            text=tr("Node Medic is built and field-tested by one person. If it helps "
                    "you build the mesh, you can chip in with Ethereum — scan the code "
                    "with a phone wallet, or send to the address below."),
            halign="center", valign="top", font_size="14sp", size_hint_y=None,
            color=theme.hex_to_rgba(theme.COLORS["text_secondary"]))
        sup_line.bind(width=lambda i, w: setattr(i, "text_size", (w, None)))
        sup_line.bind(texture_size=lambda i, ts: setattr(i, "height", ts[1] + dp(10)))
        body.add_widget(sup_line)
        qr = Image(source=DONATE_QR, size_hint_y=None, height=dp(190),
                   allow_stretch=True, keep_ratio=True)
        body.add_widget(qr)
        addr = Label(text=ETH_ADDR, halign="center", valign="middle",
                     font_size="13sp", size_hint_y=None, height=dp(30),
                     color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
        addr.bind(size=lambda i, v: setattr(i, "text_size", v))
        body.add_widget(addr)
        addr_note = Label(text=tr("ETH / EVM address"), font_size="11sp",
                          size_hint_y=None, height=dp(22),
                          color=theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.7))
        body.add_widget(addr_note)

        hint = Label(text=tr("tap anywhere to go back"),
                     font_size="12sp", size_hint_y=None, height=dp(24),
                     color=theme.hex_to_rgba(theme.COLORS["text_secondary"], 0.7))
        body.add_widget(hint)
        scroll.add_widget(body)
        self.add_widget(scroll)

        # the poster's card row, mirrored: five mode buttons along the bottom
        row = BoxLayout(orientation="horizontal", size_hint_y=None,
                        height=dp(52), spacing=dp(6))
        # Labels read POSTER_WORD_FOR (repaint 2026-09-13) so this row cannot
        # say a word the poster no longer paints; PROBE has no card, so it
        # keeps its own name.
        from ui.home_zones import POSTER_WORD_FOR
        for key in ("vitals", "scan", "birth", "triage", "probe"):
            label = POSTER_WORD_FOR.get(key, key.upper())
            btn = Button(text=label, font_size="13sp", background_normal="",
                         background_color=theme.hex_to_rgba(theme.COLORS["surface"]),
                         color=theme.hex_to_rgba(theme.COLORS["text_primary"]))
            btn.bind(on_release=lambda _b, k=key: self._select(k))
            row.add_widget(btn)
        self._mode_row = row
        self.add_widget(row)

    def _select(self, key):
        if self._on_select:
            self._on_select(key)

    def on_touch_up(self, touch):
        # the bottom mode row handles its own taps; a genuine tap anywhere else =
        # back. A drag (scrolling the support/QR section) must NOT go back, so we
        # only treat near-stationary touches as taps and let drags reach the
        # ScrollView to finish their gesture.
        if self._mode_row.collide_point(*touch.pos):
            return super().on_touch_up(touch)
        moved = (abs(touch.x - touch.ox) + abs(touch.y - touch.oy)) > dp(12)
        if not moved and self.collide_point(*touch.pos) and self._on_back:
            self._on_back()
            return True
        return super().on_touch_up(touch)
