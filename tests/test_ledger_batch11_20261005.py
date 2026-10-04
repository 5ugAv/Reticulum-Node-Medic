"""Readiness ledger, 2026-10-05 early, batch eleven — the lock-screen explainer
that clipped in six languages, VITALS strings a translated user still saw in
English, and the map's storage verdict."""
import json
import os

from tests.srcutil import ROOT, src

SHIPPED = ("es", "fr", "de", "ja", "ru", "pl", "id", "sv")


def test_the_reset_explainer_grows_to_its_translation():
    v = src("ui/screens/vault_unlock_screen.py")
    para = v[v.index("Resetting erases this medic's records for good"):]
    assert "h=76" not in para[:400] and "grow_to_text(_line(" in v
    assert "from ui.text_fit import grow_to_text" in v


def test_vitals_rows_and_the_watch_line_translate():
    vs = src("ui/screens/vitals_screen.py")
    assert 'tr("Quiet · not heard in {h}h+   ({count})")' in vs
    assert 'tr("x{n} services")' in vs
    assert "_location_words(node.get(\"location\", \"\"))" in vs
    assert 'tr("heard on the mesh")' in vs and 'tr("LXMF propagation announces")' in vs
    app = src("ui/app.py")
    assert "in about {days} more day(s)" in app
    for code in SHIPPED:
        with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"), encoding="utf-8") as f:
            cat = json.load(f)
        for k in ("x{n} services", "heard on the mesh", "LXMF propagation announces"):
            assert k in cat, (code, k)


def test_the_map_storage_verdict_is_a_translatable_template():
    from ui.map_download import storage_summary
    v = storage_summary(10.0, 1000.0)
    assert v["ok"] is True and v["key"].startswith("Uses about {size}")
    assert v["text"] == v["key"].format(**v["args"])
    big = storage_summary(900.0, 1000.0)
    assert big["ok"] is False and "{budget}" in big["key"]
    sc = src("ui/screens/scan_screen.py")
    assert sc.count('tr(verdict["key"]).format(**verdict["args"])') == 2
    assert "verdict['text']" not in sc
