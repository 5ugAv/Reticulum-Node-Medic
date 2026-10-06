"""The new medic's own screen during a clone (keeper, 2026-10-06).

Before the copy, the new medic has no app and no window system: only the
kernel console. The old progress was a line of tiny console text per step,
which read as the same line repeated. This draws ONE full-screen picture per
step straight into the framebuffer instead:

* behind, the real Node Medic home page, heavily pixelated and dim at the
  first step and sharpening a little with every step — the medic visibly
  "fills in" until the app itself takes over;
* in front, a dark panel: what is happening, a big progress bar, and the step
  list (done / now / still to come) in type you can read from arm's length.

Drawn on THIS medic with Pillow (the new one has no imaging library yet),
sent across as raw pixels and written to /dev/fb0. Pure functions here, so
the picture is unit-tested and previewed with no hardware.
"""
from __future__ import annotations

import os
from typing import Optional, Sequence

WIDTH, HEIGHT = 720, 1280

_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
#: A real screenshot of this medic's home page (720x1280).
BACKDROP = os.path.join(_HERE, "assets", "clone_base", "home_backdrop.png")

_FONT_DIRS = ("/usr/share/fonts/truetype/dejavu",
              os.path.join(_HERE, "assets", "fonts"),
              "/Library/Fonts", "/System/Library/Fonts/Supplemental")

#: What the keeper reads for each step: plain words, no internals.
STEP_WORDS = {
    "find_new_medic": "Finding the new medic",
    "verify_target_pi5": "Checking it is a Raspberry Pi 5",
    "carry_the_time": "Setting its clock",
    "transfer_tool": "Copying Node Medic",
    "transfer_firmware_cache": "Copying the radio firmware",
    "carry_the_toolchain": "Copying the build tools (the longest part)",
    "install_dependencies": "Installing the software",
    "carry_touch_cure": "Setting up the touchscreen",
    "install_display_stack": "Installing the screen",
    "install_carried_packages": "Installing the GPS and radio software",
    "copy_monitoring_db": "Copying the node records",
    "copy_offline_maps": "Copying the offline maps",
    "copy_kin_roster": "Copying the list of your nodes",
    "generate_fresh_identity": "Giving it its own mesh address",
    "stamp_lineage": "Recording which medic made it",
    "record_child_trust": "Linking it to this medic",
    "configure_autostart": "Starting Node Medic at power-on",
    "bake_recovery_bootorder": "Setting up recovery start-up",
    "install_card_helper": "So it can make medics too",
    "ensure_ssh_keypair": "Giving it its own key",
    "final_verification": "Checking everything",
    "restart_into_tool": "Restarting into Node Medic",
    "confirm_tool_running": "Making sure Node Medic stays open",
}


def words_for(step: str) -> str:
    return STEP_WORDS.get(step, step.replace("_", " ").capitalize())


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    names = (("DejaVuSans-Bold.ttf", "Arial Bold.ttf") if bold
             else ("DejaVuSans.ttf", "Arial.ttf"))
    for d in _FONT_DIRS:
        for n in names:
            p = os.path.join(d, n)
            if os.path.isfile(p):
                return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def pixel_size(done: int, total: int) -> int:
    """Block size of the pixelated home page: 48 px at the start, 1 (sharp)
    when every step is done."""
    if total <= 0:
        return 48
    frac = max(0.0, min(1.0, done / total))
    return max(1, round(48 * (1.0 - frac)))       # sharpens evenly, step by step


def _backdrop(done: int, total: int, path: Optional[str]):
    from PIL import Image, ImageEnhance
    try:
        img = Image.open(path or BACKDROP).convert("RGB").resize((WIDTH, HEIGHT))
    except Exception:                                         # noqa: BLE001
        return Image.new("RGB", (WIDTH, HEIGHT), (2, 14, 6))
    block = pixel_size(done, total)
    if block > 1:
        small = img.resize((max(1, WIDTH // block), max(1, HEIGHT // block)),
                           Image.BILINEAR)
        img = small.resize((WIDTH, HEIGHT), Image.NEAREST)
    frac = max(0.0, min(1.0, done / total)) if total else 0.0
    # visible from the first step, never so bright it fights the step list
    return ImageEnhance.Brightness(img).enhance(0.45 + 0.3 * frac)


def render_frame(steps: Sequence[str], current: int, backdrop: Optional[str] = None,
                 failed: bool = False):
    """One picture: *steps* in order, *current* = index of the step running
    now (len(steps) when all are done)."""
    from PIL import Image, ImageDraw
    total = len(steps)
    done = max(0, min(current, total))
    img = _backdrop(done, total, backdrop).convert("RGBA")
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    green, dim, amber, red = (150, 255, 90), (120, 150, 120), (255, 196, 64), (255, 90, 80)
    m = 28
    # panel
    d.rounded_rectangle((m - 8, 60, WIDTH - m + 8, HEIGHT - 60), radius=22,
                        fill=(0, 10, 4, 175), outline=(60, 120, 60, 255), width=2)
    y = 86
    d.text((WIDTH // 2, y), "NODE MEDIC", font=_font(46, True), fill=green, anchor="mt")
    y += 60
    d.text((WIDTH // 2, y), "is being made", font=_font(30), fill=(220, 240, 220),
           anchor="mt")
    y += 52
    d.text((WIDTH // 2, y), "Leave both medics plugged in", font=_font(24, True),
           fill=amber, anchor="mt")
    y += 50
    # progress bar
    bx0, bx1, bh = m + 16, WIDTH - m - 16, 34
    d.rounded_rectangle((bx0, y, bx1, y + bh), radius=bh // 2, fill=(20, 40, 24, 255),
                        outline=(80, 140, 80, 255), width=2)
    fill_w = int((bx1 - bx0) * (done / total if total else 0))
    if fill_w > bh:
        d.rounded_rectangle((bx0, y, bx0 + fill_w, y + bh), radius=bh // 2,
                            fill=(red if failed else green) + (255,))
    y += bh + 12
    shown = min(done + 1, total)
    d.text((WIDTH // 2, y), f"Step {shown} of {total}", font=_font(26, True),
           fill=(230, 250, 230), anchor="mt")
    y += 50

    # step list: window around the current step so it never runs off the panel
    row_h, f_row, f_now = 40, _font(25), _font(27, True)
    rows_fit = (HEIGHT - 90 - y) // row_h
    start = max(0, min(done - rows_fit // 3, total - rows_fit))
    for i in range(start, min(total, start + rows_fit)):
        if i < done:
            kind, col, font = "done", (110, 210, 110), f_row
        elif i == done:
            kind, col, font = ("fail" if failed else "now"), (red if failed else amber), f_now
        else:
            kind, col, font = "todo", dim, f_row
        if i == done:
            d.rounded_rectangle((m, y - 4, WIDTH - m, y + row_h - 6), radius=10,
                                fill=(40, 60, 20, 230))
        _mark(d, kind, m + 30, y + 16, col)
        text = _fit(d, words_for(steps[i]), font, WIDTH - 2 * m - 70)
        # a soft shadow keeps the words readable once the picture is sharp
        d.text((m + 58, y + 2), text, font=font, fill=(0, 0, 0, 220))
        d.text((m + 56, y), text, font=font, fill=col)
        y += row_h
    return Image.alpha_composite(img, layer).convert("RGB")


def _mark(d, kind: str, cx: int, cy: int, col) -> None:
    """Drawn, not typed: a font without ✓ or ▶ shows an empty box."""
    if kind == "done":
        d.line((cx - 9, cy, cx - 3, cy + 7, cx + 10, cy - 8), fill=col, width=5,
               joint="curve")
    elif kind == "now":
        d.polygon((cx - 8, cy - 11, cx - 8, cy + 11, cx + 11, cy), fill=col)
    elif kind == "fail":
        d.line((cx - 8, cy - 8, cx + 8, cy + 8), fill=col, width=5)
        d.line((cx - 8, cy + 8, cx + 8, cy - 8), fill=col, width=5)
    else:
        d.ellipse((cx - 4, cy - 4, cx + 4, cy + 4), fill=col)


def _fit(draw, text: str, font, width: int) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text.rstrip() + "…"


def framebuffer_bytes(img, bits_per_pixel: int = 32) -> bytes:
    """Raw pixels for /dev/fb0. The Pi 5's console framebuffer is 32-bit
    XRGB8888, little-endian: bytes run B, G, R, X."""
    img = img.convert("RGB").resize((WIDTH, HEIGHT))
    if bits_per_pixel == 16:
        return img.tobytes("raw", "BGR;16")
    return img.tobytes("raw", "BGRX")

