"""Sending an app to a phone must survive Android's two refusals.

Operator, 2026-08-06: "there needs to be instructions to show the user how to
allow their phone to install those apps because the standard scanning of that QR
code will give you a warning saying this app could be harmful. If you click okay
and try to install it, the phone will say unknown app can't install."

Both refusals are real, they arrive in that order, and neither explains itself:

  1. The BROWSER warns the file may harm the device  -> "Download anyway"
  2. ANDROID refuses to install from an unknown source -> Settings ->
     "Allow from this source" -> back -> Install

The old copy said "then allow install from unknown sources", which assumes the
operator already knows how. On Android 8+ there is no global setting for it any
more: permission is PER-APP, and it only surfaces part-way through the install,
worded as a flat "can't install". Someone who has never sideloaded reads the
first warning as "this is malware" and stops — with a perfectly good APK sitting
in their downloads.

This is the one screen where the medic hands something to a device it does not
control, so the instruction has to carry the operator across both.
"""
from tests.srcutil import src

SCREEN = "ui/screens/comms_screen.py"


def _text():
    """Only what the operator can SEE. Comments quote the old wording and
    would satisfy — or wrongly fail — these assertions."""
    out = []
    for ln in src(SCREEN).splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        out.append(ln)
    return "\n".join(out)


def test_the_browser_warning_is_named_with_its_button():
    t = _text()
    assert "harmful" in t, "the first warning is not mentioned"
    assert "Download anyway" in t, "the button to press is not named"


def test_the_install_refusal_is_named_with_its_route_out():
    t = _text()
    assert "unknown apps" in t, "the second refusal is not mentioned"
    assert "Allow from this source" in t, (
        "the actual toggle is not named — 'allow unknown sources' is not what "
        "the phone calls it, and there is no global setting to find")


def test_the_steps_are_numbered_and_in_the_order_they_happen():
    """The operator meets these one after another; out of order is useless."""
    t = _text()
    one, two = t.index("1.  Put your phone"), t.index("2.  The browser")
    three, four = t.index("3.  Open"), t.index("4.  Turn on")
    assert one < two < three < four


def test_the_warnings_are_explained_as_normal():
    """Otherwise the honest reading of two security warnings is 'stop'."""
    t = _text()
    assert "normal" in t and "Play Store" in t


def test_it_does_not_claim_a_setting_that_no_longer_exists():
    """'Unknown sources' as a global toggle was removed in Android 8. Sending
    someone to look for it in Settings wastes their time and undermines trust
    in the rest of the instructions."""
    t = _text()
    assert "unknown sources" not in t.lower(), (
        "still tells the operator to enable a global 'unknown sources' setting")


# -- "join the medic's Wi-Fi" meant nothing to anyone -------------------------

def test_the_network_step_says_WHICH_network():
    """Operator: "join the medic's Wi-Fi. What does that mean?" It read as
    though the medic broadcasts one. It does not — provisioning/wifi.py only
    ever JOINS networks. What the phone needs is to be on the SAME network the
    medic is already on, and the medic knows its name, so it says it."""
    t = _text()
    assert "same Wi-Fi as this medic: {ssid}" in t, "the network is not named"
    # split across source lines by implicit concatenation, so match a
    # contiguous fragment rather than the rendered sentence
    assert "same Wi-Fi network this medic" in t, (
        "no fallback wording for a medic that cannot read its SSID")
    assert "Join the medic's Wi-Fi" not in t, "the ambiguous wording is back"


def test_it_explains_that_mobile_data_will_not_do():
    """Operator: "doesn't the qr code just require Internet connection of any
    sort?" No — the QR points at the medic's own private address (192.168.x.x),
    which nothing outside that network can reach. A phone on 5G with no shared
    Wi-Fi fails, and nothing on screen would have explained why."""
    t = _text()
    assert "not the internet" in t
    assert "mobile data" in t


def test_it_offers_the_field_answer_when_there_is_no_shared_network():
    """The medic joins networks rather than raising one, so out in a field the
    PHONE has to be the hotspot."""
    t = _text()
    assert "hotspot" in t
