"""After birth, a node's position is settable the same three ways as at
birth: type an address, move the pin, or take the medic's satellite fix.

Operator, 2026-09-23: "User can type an address in search, move a map pin
or use GPS coordinates when the medic has satellite connection." The
Location & map popup shows the GPS road as its own button, live only while
the fix is LIVE, with the numbers and a warning on the confirm card.
"""
import json
import os

from tests.srcutil import ROOT, func_source
from monitor.geo import GpsFix
from ui.location_offer import gps_offer

POPUP = "ui/widgets/map_sharing.py"


def _fix(**kw):
    base = dict(lat=-37.7, lon=145.0, sats=8, fix_quality=1, accuracy_m=4.4)
    base.update(kw)
    return GpsFix(**base)


def test_a_live_fix_is_offered_with_its_numbers():
    o = gps_offer(_fix())
    assert o["ok"] and o["lat"] == -37.7 and o["lon"] == 145.0
    assert o["coords"].startswith("-37.7") and o["acc_m"] == 4.4


def test_a_held_or_absent_fix_is_refused_with_the_reason():
    assert gps_offer(None) == {"ok": False, "lat": None, "lon": None, "acc_m": None,
                               "coords": "", "reason": "none"}
    held = gps_offer(_fix(sats=0))
    assert not held["ok"] and held["reason"] == "held"


def test_accuracy_is_never_invented():
    o = gps_offer(_fix(accuracy_m=None))
    assert o["ok"] and o["acc_m"] is None


def test_the_popup_shows_both_roads_and_confirms_the_gps_one():
    src = open(os.path.join(ROOT, POPUP), encoding="utf-8").read()
    assert 'tr("Choose a location on the map…")' in src
    assert 'tr("Set location on the map…")' not in src
    assert 'tr("Use GPS location")' in src
    use = func_source(POPUP, "_use_gps_fix", cls="MapSharingPopup")
    assert "confirm_leave(" in use and 'tr("Set it")' in use
    assert "standing at the node" in use
    assert "self._location_confirmed(lat, lon)" in use, "the GPS road writes through the one door"
    ref = func_source(POPUP, "_refresh_gps", cls="MapSharingPopup")
    assert 'btn.disabled = not offer["ok"]' in ref
    assert "No satellite fix right now" in ref
    refresh = func_source(POPUP, "_refresh", cls="MapSharingPopup")
    assert "self._refresh_gps()" in refresh


def test_the_strings_format_in_every_catalog():
    keys = {"Choose a location on the map…": {}, "Use GPS location": {}, "Set it": {},
            "Satellite fix now: {coords} (~±{acc}m)": dict(coords="x", acc=3),
            "Satellite fix now: {coords}": dict(coords="x"),
            "No satellite fix right now — choose on the map instead.": {},
            "{coords} (~±{acc}m)": dict(coords="x", acc=3),
            "{where} — Node Medic's satellite fix right now. Only right if you are standing at the node.": dict(where="x"),
            "Set {name}'s position to where Node Medic is now?": dict(name="x")}
    for lang in ("de", "es", "fr", "id", "ja", "pl", "ru", "sv"):
        d = json.load(open(os.path.join(ROOT, f"assets/i18n/{lang}.json"), encoding="utf-8"))
        assert "Set location on the map…" not in d
        for k, args in keys.items():
            assert k in d, (lang, k)
            d[k].format(**args)
