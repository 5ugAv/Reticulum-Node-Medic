#!/usr/bin/env python3
"""Draw the front page's Wi-Fi icon onto the poster at its four states, so
it can be LOOKED at before it ships (preview before deploy). Same numbers
as the widget: ui.wifi_level. Writes one PNG with the four crops side by side.

    python3 scripts/preview_wifi_icon.py out.png
"""
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ui.wifi_level import CONNECTED, OFF, bar_geometry, bars_for, state_for  # noqa: E402

POSTER = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "assets", "ui", "front_page.png")
CX, CY, R = 360, 368, 21                     # the painted ring, measured
GREEN, DIM, GREY, GREY_DIM = (0, 200, 83, 255), (0, 200, 83, 72), (133, 143, 133, 255), (133, 143, 133, 90)
DARK = (0, 16, 0, 255)


def draw_icon(img, enabled, connected, signal):
    d = ImageDraw.Draw(img, "RGBA")
    w = h = int(R * 2 * 1.36)
    x, y = CX - w / 2, CY - h / 2
    d.ellipse([x, y, x + w, y + h], fill=DARK)
    state = state_for(enabled, connected)
    lit = bars_for(enabled, connected, signal)
    bx, by, bw, bh = x + w * 0.18, y + h * 0.2, w * 0.64, h * 0.6
    bottom = by + bh
    for i, (rx, ry, rw, rh) in enumerate(bar_geometry(bw, bh)):
        if state == OFF:
            col = GREY
        elif state == CONNECTED and i < lit:
            col = GREEN
        else:
            col = DIM if state == CONNECTED else GREY_DIM
        d.rectangle([bx + rx, bottom - rh, bx + rx + rw, bottom], fill=col)


def main(out):
    states = [("off", False, False, None), ("searching", True, False, None),
              ("weak", True, True, 40), ("strong", True, True, 90)]
    crops = []
    for name, en, co, sig in states:
        im = Image.open(POSTER).convert("RGBA")
        draw_icon(im, en, co, sig)
        crops.append(im.crop((CX - 110, CY - 70, CX + 110, CY + 70)).resize((440, 280)))
    sheet = Image.new("RGBA", (440 * 4 + 30, 280), (0, 0, 0, 255))
    for i, c in enumerate(crops):
        sheet.paste(c, (i * 450, 0))
    sheet.save(out)
    print("wrote", out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "wifi_icon_preview.png")
