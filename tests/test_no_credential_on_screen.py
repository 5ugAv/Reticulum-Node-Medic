"""A Wi-Fi password must never be drawn on the panel.

Operator, with a photo, 2026-09-08: "I just noticed that the wifi password is
in plain view here." The RTNode build-outcome screen listed the onboarding
fields to type into the board's captive portal by hand, and rendered `psk`
verbatim among them. The screen gets photographed — by whoever is standing
behind the operator, and by the operator themselves to send to me.

Showing it bought nothing: the manual fallback has the operator typing their
OWN network's password, which they already know.

Certificates were checked at the time and none stored a psk (20 on disk, 0
carrying one), so the screen was the whole exposure — but the viewer would
have rendered one had it ever been written, so that path is masked too.

Source-level pins: both screens are Kivy, which does not import in CI.
"""

import textwrap

from tests.srcutil import func_source

BIRTH = "ui/screens/birth_screen.py"
CERTVIEW = "ui/screens/cert_view_screen.py"


def test_the_outcome_panel_masks_the_wifi_password():
    src = open(BIRTH).read()
    i = src.index('"node_name", "ssid", "psk", "freq"')
    window = src[i:i + 1600]
    assert 'k == "psk"' in window, \
        "the onboarding fallback panel renders psk verbatim again"
    # the mask has to be applied BEFORE the line is added, not after
    assert window.index('k == "psk"') < window.index("self.list.add_widget"), \
        "mask the value before drawing it"


def test_the_certificate_viewer_masks_credentials():
    src = open(CERTVIEW).read()
    assert "_MASKED" in src and '"psk"' in src
    mask = textwrap.dedent(func_source(CERTVIEW, "_mask"))
    assert "_MASKED" in mask


def test_both_certificate_render_loops_go_through_the_mask():
    """The second loop draws whatever the build recorded under keys nobody
    listed. A credential must not escape just by arriving there."""
    src = open(CERTVIEW).read()
    body = src[src.index("for key, label in _PRETTY:"):]
    body = body[:2000]
    assert body.count("_mask(") >= 2, \
        "both the pretty loop and the catch-all loop must mask"


def test_the_psk_is_not_written_into_birth_certificates():
    """Defence in depth: masking the screens only helps while nothing stores
    it. cert_store must not be handed the password."""
    src = open("ui/cert_store.py").read()
    assert '"psk"' not in src and "'psk'" not in src, \
        "cert_store now references psk — a credential would be written to disk"
