"""The setup walkthrough's progress dots must sit on their own row after an
in-place re-render (Next, Back, reset, the socket's jump).

On the glass (2026-10-05) every re-render drew the dots over the "Step 1 of 20"
counter. Cause: the ellipse was placed from center_y inside the size dispatch,
where that alias still holds the value cached for the widget's default 100 px
height. The geometry itself is proven on the medic by the sandbox dots check
(scratch hcheck_dots.py, run by the deploy script); this pins the two source
facts that check depends on, since no test here may import a Kivy widget.
"""
import re


def _dots_class():
    src = open("ui/widgets/wizard_step.py", encoding="utf-8").read()
    start = src.index("class _Dots(")
    end = src.index("\nclass ", start + 1)
    return src[start:end]


def test_dot_placement_is_bound_to_pos_and_size():
    body = _dots_class()
    assert re.search(r"bind\(\s*pos=self\._place,\s*size=self\._place\s*\)", body), body


def test_dot_placement_never_reads_the_center_aliases():
    body = _dots_class()
    place = body[body.index("def _place("):]
    code = "\n".join(l for l in place.splitlines() if not l.strip().startswith("#"))
    assert "center_x" not in code and "center_y" not in code
    assert "wi.x" in place and "wi.y" in place and "wi.width" in place and "wi.height" in place
