"""The finished clone gets a big congratulation (keeper, 2026-10-06): small text
at the foot of the list "doesn't look like anything's changed"."""
from tests.srcutil import src


def test_the_finish_opens_the_congratulation_with_what_to_do_next():
    m = src("ui/screens/mitosis_screen.py")
    assert "show_clone_cheer(name)" in m
    c = src("ui/widgets/clone_cheer.py")
    for words in ("Congratulations!", "Unplug the ethernet cable",
                  "Heltec Wireless Tracker as its LoRa radio and GPS"):
        assert words in c, words
    assert 'COLORS["accent"]' in c and "Triangle" in c       # a lime word bubble
    assert "grow_to_text" in c and "auto_dismiss=False" in c
