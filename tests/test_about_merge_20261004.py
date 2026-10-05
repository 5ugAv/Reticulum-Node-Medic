"""Settings > About shows the same thanks + support page the front page's
cross opens (operator, 2026-10-04) — one body, two doors — and the Easter
egg still works."""
import ast
import re

from tests.srcutil import src

CREDITS = "ui/screens/credits_screen.py"
ABOUT = "ui/screens/about_screen.py"


def test_the_thanks_body_is_one_widget_shared_by_both_doors():
    c = src(CREDITS)
    assert "class CreditsBody(BoxLayout):" in c
    assert "body.add_widget(CreditsBody())" in c          # the Easter egg
    a = src(ABOUT)
    assert "from ui.screens.credits_screen import CreditsBody" in a
    assert "col.add_widget(CreditsBody())" in a           # Settings > About


def test_about_keeps_its_build_facts_above_the_thanks():
    a = src(ABOUT)
    for field in ("Software version", "Test suite", "Uptime", "Licence", "Repository"):
        assert f'_field(tr("{field}")' in a, field
    assert a.index('_field(tr("Repository")') < a.index("CreditsBody()")
    assert 'tr("With thanks")' in a


def test_the_support_section_is_intact_and_the_address_unchanged():
    c = src(CREDITS)
    addr = re.search(r'^ETH_ADDR = "(0x[0-9a-fA-F]{40})"', c, re.M).group(1)
    assert addr == "0x93D7c938A85B1AB74950CC9eA0030DfB52bFC42E"
    body = c[c.index("class CreditsBody"):c.index("class CreditsScreen")]
    assert "Image(source=DONATE_QR" in body and "text=ETH_ADDR" in body
    assert 'tr("Support this work")' in body
    # the CREDITS literal stays a module-level list (test_credits_truth reads it)
    assert ast.literal_eval(re.search(r"^CREDITS = (\[.*?^\])", c,
                                      re.S | re.M).group(1))


def test_the_easter_egg_route_still_opens_the_credits_screen():
    app = src("ui/app.py")
    assert 'Screen(name="credits")' in app and "CreditsScreen(" in app
    zones = src("ui/home_zones.py")
    assert 'return "credits"' in zones
    c = src(CREDITS)
    assert "def on_touch_up(self, touch):" in c           # tap anywhere = back
    assert 'tr("tap anywhere to go back")' in c
