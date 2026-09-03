"""Regenerate assets/i18n/_critical.json — the strings a language MUST cover.

Run from the repo root:  python3 tools/gen_critical_path.py

WHY THIS EXISTS. Catalogs used to be all-or-nothing: every shipped language had
to have an identical key set, so a Swahili speaker could not contribute fifty
strings and see them appear. That is a poor way to get a language finished, and
the languages this tool most needs are the ones least likely to arrive complete
in one go.

So a language is offered once it covers the CRITICAL PATH — everything a person
meets before they have learned anything about the tool. Past that point, missing
keys fall back to English, which the catalog design already does safely.
"""
import ast
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Where a person lands before they know anything. Adding a module here raises
#: the bar for every partly-translated language, so add deliberately.
CRITICAL_MODULES = (
    "ui/screens/language_screen.py",
    "ui/screens/home_screen.py",
    "ui/screens/settings_screen.py",
    "ui/screens/comms_screen.py",
)

OUT = os.path.join(ROOT, "assets", "i18n", "_critical.json")


def wrapped_strings(rel_paths=CRITICAL_MODULES):
    out = set()
    for rel in rel_paths:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        tree = ast.parse(open(path, encoding="utf-8").read())
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call)
                    and getattr(n.func, "id", "") in ("tr", "_")
                    and n.args and isinstance(n.args[0], ast.Constant)
                    and isinstance(n.args[0].value, str)
                    and n.args[0].value.strip()):
                out.add(n.args[0].value)
    return sorted(out)


if __name__ == "__main__":
    strings = wrapped_strings()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(strings, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    print(f"{len(strings)} critical strings -> {OUT}")
