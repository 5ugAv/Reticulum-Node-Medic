"""Every birth-screen phase label is translated in every full catalog.

test_i18n's AST walk collects `tr("literal")` calls; the birth screen's
_PHASE_LABELS dict is looked up first and wrapped in tr() second, so its
values never reach that walk — and the install_time_trust label shipped
in English for all eight languages (review, 2026-09-23). This reads the
dict itself (a Kivy module: as source, never imported)."""
import ast
import json
import os

from tests.srcutil import ROOT, src

CATALOGS = ("de", "es", "fr", "id", "ja", "pl", "ru", "sv")


def _phase_labels() -> dict:
    tree = ast.parse(src("ui/screens/birth_screen.py"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == "_PHASE_LABELS":
            return ast.literal_eval(node.value)
    raise AssertionError("_PHASE_LABELS not found in ui/screens/birth_screen.py")


def test_every_phase_label_is_in_all_eight_catalogs():
    labels = _phase_labels()
    assert labels and "install_time_trust" in labels
    for code in CATALOGS:
        with open(os.path.join(ROOT, "assets", "i18n", f"{code}.json"), encoding="utf-8") as fh:
            cat = json.load(fh)
        missing = [v for v in labels.values() if v not in cat]
        assert not missing, f"{code}.json lacks phase labels: {missing}"


def test_the_labels_are_wrapped_in_tr_at_the_point_of_use():
    assert "tr(_PHASE_LABELS.get(" in src("ui/screens/birth_screen.py")
