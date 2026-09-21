"""The RNode board shortlist shows each board's NAME under its photo.
Operator, 2026-09-21, watching a friend meet the picker with a Heltec V4:
"the user can only use imagery to identify their boards … there was no
words until the actual board is selected." A picture is a hint; the
name is the answer. Pinned in code (Kivy screen, not buildable here)."""
import re


def test_every_photo_card_in_the_shortlist_has_its_name_under_it():
    src = open("ui/screens/birth_screen.py").read()
    m = re.search(r"    def _add_rnode_board_pick\(.*?(?=\n    def )", src, re.S)
    assert m, "_add_rnode_board_pick missing"
    body = m.group(0)
    loop = body[body.index("for b in with_photo[i:i + 3]"):]
    assert "b.display_name" in loop.split("for _ in range")[0], (
        "the card's column must carry display_name, not just the photo")
    assert "dp(120) + dp(26)" in body, "the row grows to hold the caption"
