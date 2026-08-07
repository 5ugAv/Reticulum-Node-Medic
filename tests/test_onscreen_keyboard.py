"""On-screen keyboard pure logic — the modal parent-walk must TERMINATE.

Kivy's Window.parent is the Window ITSELF, so walking ``w = w.parent`` from a
field that is NOT inside a ModalView reaches the Window and — without the
self-parent guard — spins forever with the GIL held: the 2026-07-30 BIRTH
"Name this node" freeze (main thread pegged, whole UI dead, SIGTERM ignored).

The widget tree here is plain stub objects; Kivy modules are stubbed before
import, same pattern as test_scan_lines."""

import sys
import types


def _install_kivy_stubs():
    class _Dummy:
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            return _Dummy()

    def _module(name):
        m = types.ModuleType(name)
        m.__getattr__ = lambda attr: _Dummy
        return m

    for name in (
        "kivy", "kivy.app", "kivy.clock", "kivy.core", "kivy.core.window",
        "kivy.graphics", "kivy.metrics", "kivy.uix", "kivy.uix.boxlayout",
        "kivy.uix.button", "kivy.uix.modalview",
    ):
        sys.modules.setdefault(name, _module(name))


_install_kivy_stubs()

from ui.onscreen_keyboard import OnScreenKeyboard  # noqa: E402


class _Node:
    """Featherweight widget stand-in with an explicit parent link."""
    def __init__(self, parent=None):
        self.parent = parent


def _walk(target):
    """Run the real parent-walk with a minimal fake self. Returns normally only
    if the walk terminates (regression = this call never returns and the test
    times out loudly)."""
    fake_self = _Node()          # keyboard stand-in: .parent is None
    OnScreenKeyboard._raise_above_modal(fake_self, target)


def test_walk_terminates_when_root_is_its_own_parent():
    # Kivy reality: root widget chain ends at Window, and Window.parent is
    # Window. A field NOT in a modal must terminate the walk at that self-loop.
    window = _Node()
    window.parent = window                     # the Kivy self-parent quirk
    root = _Node(parent=window)
    field = _Node(parent=_Node(parent=root))   # field -> layout -> root -> window
    _walk(field)                               # returns = fixed; hangs = broken


def test_walk_terminates_on_orphan_field():
    _walk(_Node())                             # parent None -> immediate exit


# --- the way OFF a multiline field ---------------------------------------
#
# On a ONE-LINE field ENTER closes the keyboard, and it is labelled DONE for
# exactly that reason (walkthrough, 2026-08-02: the keyboard covers the screen's
# own buttons, so a key that says ENTER gives the operator no visible way off
# the step). A MULTILINE field cannot borrow that key — there ENTER has to
# insert a new line — so those fields had NO dismiss key at all. The operator
# hit it on BIRTH > Add notes (2026-08-08): text typed, notes unsaveable, page
# unleaveable. Same dead end, other door.

from ui import onscreen_keyboard as osk  # noqa: E402


class _Field:
    def __init__(self, multiline):
        self.multiline = multiline


def _rows_for(field, layer="text"):
    fake = _Node()
    fake.target = field
    fake._layer = layer
    return OnScreenKeyboard._rows(fake)


def _flat(rows):
    return [k for row in rows for k in row]


def test_multiline_field_gets_a_done_key():
    assert osk._DONE in _flat(_rows_for(_Field(multiline=True)))


def test_single_line_field_does_not_get_one():
    # ENTER already closes the keyboard there and renders as "DONE"; a second
    # DONE would be two keys claiming the same job.
    assert osk._DONE not in _flat(_rows_for(_Field(multiline=False)))


def test_no_target_does_not_crash_or_add_a_done_key():
    assert osk._DONE not in _flat(_rows_for(None))


def test_every_layer_offers_the_way_out_when_multiline():
    # Switching to symbols or numerics must not strip the only exit.
    for layer in ("text", "symbols", "numeric"):
        assert osk._DONE in _flat(_rows_for(_Field(multiline=True), layer)), layer


def test_done_is_appended_not_substituted():
    # ENTER must survive: on a multiline field it is what makes a new line.
    rows = _rows_for(_Field(multiline=True))
    assert osk._ENTER in _flat(rows)
    assert rows[-1][-1] == osk._DONE


def test_done_does_not_leak_into_the_shared_layout_tables():
    # _rows() must copy, not mutate the module-level lists — otherwise the
    # first multiline field poisons every later single-line one.
    _rows_for(_Field(multiline=True))
    _rows_for(_Field(multiline=True))
    for table in (osk._TEXT_LOWER, osk._SYMBOLS, osk._NUMERIC):
        assert osk._DONE not in _flat(table)
    assert osk._DONE not in _flat(_rows_for(_Field(multiline=False)))


def test_done_has_a_readable_label_not_a_tofu_glyph():
    # The sentinels render as boxes in the default font, so each needs an ASCII
    # word in _DISPLAY. A DONE key nobody can read is the same dead end.
    assert osk._DISPLAY.get(osk._DONE) == "DONE"


def test_done_key_hides_the_keyboard():
    class _KB:
        hidden = False
        target = _Field(multiline=True)
        _last_key_label = None
        _last_key_t = 0.0

        def hide(self):
            self.hidden = True

        def _refocus(self):
            raise AssertionError("must return before refocusing a dismissed field")

    kb = _KB()
    OnScreenKeyboard._on_key(kb, osk._DONE)
    assert kb.hidden is True


# --- and no future field may skip this keyboard --------------------------

def test_every_multiline_textinput_is_bound_to_the_onscreen_keyboard():
    """A multiline TextInput that never calls bind_field gets Kivy's own
    keyboard, which has no DONE key — reopening the dead end this file exists
    to close. The medic's panel has no physical keys, so an unbound editable
    field is unusable regardless.

    Checked with the AST rather than a text grep: an earlier guard in this repo
    matched its own explanatory comment and passed while the thing it guarded
    was broken (three times in one session)."""
    import ast
    import os

    ui_dir = os.path.join(os.path.dirname(__file__), "..", "ui")
    offenders = []
    for root, _dirs, files in os.walk(ui_dir):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path) as fh:
                try:
                    tree = ast.parse(fh.read())
                except SyntaxError:               # pragma: no cover
                    continue
            bound = {
                a.id
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "bind_field"
                for a in n.args
                if isinstance(a, ast.Name)
            } | {
                ast.unparse(a)
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "bind_field"
                for a in n.args
            }
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call)
                        and getattr(node.func, "id", "") == "TextInput"):
                    continue
                multi = any(k.arg == "multiline"
                            and getattr(k.value, "value", False) is True
                            for k in node.keywords)
                if not multi:
                    continue
                # find what it was assigned to, then require a bind_field on it
                target = None
                for a in ast.walk(tree):
                    if isinstance(a, ast.Assign) and a.value is node:
                        target = ast.unparse(a.targets[0])
                        break
                if target is None or target not in bound:
                    offenders.append(
                        f"{os.path.relpath(path, ui_dir)}:{node.lineno} "
                        f"({target or 'unassigned'})")
    assert not offenders, (
        "multiline TextInput(s) not bound to the on-screen keyboard — they will "
        "have no DONE key and no way to dismiss: " + ", ".join(offenders))
