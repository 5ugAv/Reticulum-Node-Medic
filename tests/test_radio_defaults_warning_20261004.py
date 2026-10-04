"""Settings > Default radio parameters: the yellow warning at the top must
stand out from the settings under it (operator, 2026-10-04: "put a red circle
around that and make the font slightly bigger ... maybe bold")."""


def test_the_defaults_warning_wears_a_red_ring_and_bigger_bold_text():
    s = open("ui/screens/radio_defaults_screen.py").read()
    i = s.index("# prominent warning")
    block = s[i:i + 2200]
    assert 'COLORS["red"]' in block and "Line(width=dp(2.2))" in block
    assert "rounded_rectangle = (warn.x, warn.y" in block
    assert 'size="15.5sp", color="warning_yellow", bold=True' in block
    assert 'size="13.5sp"' not in block
