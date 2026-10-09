"""Judge the medic's scoped sudoers the way sudo does — not the way it reads.

Two rules of sudoers(5) the earlier hand-rolled matcher got backwards, both of
which make a policy look TIGHTER than it is:

* The arguments are matched as ONE string (argv[1:] joined by single spaces),
  and a wildcard matches spaces too. ``/bin/cat /var/log/messages*`` also
  allows ``cat /var/log/messages /etc/shadow`` — the man page's own example,
  under "Wildcards". A per-argument comparison says "denied" where sudo says
  "allowed".
* A command given with NO arguments allows ANY arguments; only ``""`` means
  "none at all".

Sudoers escapes (``\\:``, ``\\,``, ``\\=``, ``\\\\``) are undone before matching,
and an argument list written ``^...$`` is a regular expression (sudo 1.9.10+),
matched here with Python's re — the same as POSIX ERE for the plain patterns a
policy should use.

Rendering goes through the real provisioning/security/render_sudoers.sh, so
the script a clone's install runs is the one under test.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "provisioning", "sudoers.d", "nodemedic")
RENDER = os.path.join(ROOT, "provisioning", "security", "render_sudoers.sh")


def split_unescaped(text: str, sep: str = ",") -> List[str]:
    """Split on *sep* where it is not escaped with a backslash."""
    parts, cur, i = [], [], 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            cur.append(text[i:i + 2])
            i += 2
            continue
        if ch == sep:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def unescape(text: str) -> str:
    return re.sub(r"\\(.)", r"\1", text)


class Cmnd:
    """One command in a Cmnd_Alias: a path and (maybe) an argument pattern."""

    def __init__(self, spec: str):
        self.spec = " ".join(spec.split())
        path, _, args = self.spec.partition(" ")
        self.path = path
        #: None = no arguments written = ANY arguments allowed
        self.args: Optional[str] = args or None

    @property
    def is_regex(self) -> bool:
        return bool(self.args) and self.args.startswith("^") and self.args.endswith("$")

    @property
    def spans(self) -> bool:
        """Could a caller append arguments of their own? True for any `*`/`?`
        in a glob pattern, and for a command written with no arguments."""
        if self.args is None:
            return True
        if self.args == '""' or self.is_regex:
            return False
        return any(ch in unescape(self.args) for ch in "*?")

    def matches(self, argv: List[str]) -> bool:
        if not argv or argv[0] != self.path:
            return False
        given = " ".join(argv[1:])
        if self.args is None:
            return True
        if self.args == '""':
            return given == ""
        if self.is_regex:
            return re.fullmatch(unescape(self.args)[1:-1], given) is not None
        return fnmatch.fnmatchcase(given, unescape(self.args))


class Policy:
    """The aliases, the account's Defaults and THE grant line for one user,
    from a rendered policy."""

    def __init__(self, text: str, user: str):
        joined = text.replace("\\\n", " ")
        self.aliases = {}
        #: settings from ``Defaults:<user> ...`` lines, in order. A Defaults
        #: line for anyone else, or a global one, is an unexpected line: this
        #: file may only set things for its own account.
        self.defaults: List[str] = []
        grant = None
        for line in joined.splitlines():
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            m = re.match(r"Cmnd_Alias\s+([A-Z0-9_]+)\s*=\s*(.+)$", s)
            if m:
                self.aliases[m.group(1)] = [Cmnd(c) for c in split_unescaped(m.group(2))]
                continue
            m = re.match(rf"Defaults:{re.escape(user)}\s+(.+)$", s)
            if m:
                self.defaults += split_unescaped(m.group(1))
                continue
            m = re.match(rf"{re.escape(user)}\s+ALL\s*=\s*\(root\)\s+NOPASSWD:\s*(.+)$", s)
            if m:
                if grant is not None:
                    raise AssertionError(f"two grant lines for {user}")
                grant = split_unescaped(m.group(1))
                continue
            raise AssertionError(f"unexpected line in the policy: {s!r}")
        if grant is None:
            raise AssertionError(f"no grant line for {user}")
        self.granted_aliases = grant
        self.commands: List[Cmnd] = []
        for item in grant:
            if item not in self.aliases:
                raise AssertionError(f"{item} is granted but never defined")
            self.commands += self.aliases[item]

    def allows(self, argv: List[str]) -> bool:
        return any(c.matches(argv) for c in self.commands)


def render(user: str, out_path: str, backlight: str = "panel_backlight@1") -> str:
    """The policy as a medic running as *user* would install it."""
    r = subprocess.run(["bash", RENDER, TEMPLATE, out_path, backlight, user],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"render_sudoers.sh failed for {user}: {r.stderr}")
    with open(out_path, encoding="utf-8") as fh:
        return fh.read()
