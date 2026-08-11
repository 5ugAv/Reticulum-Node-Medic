"""The repo must not carry the operator's life in it.

This tool is meant to go public, and the code is full of bench stories — which
is the best thing about it and also how personal detail gets in. Two scrubs
have already run over this tree (2026-08-06, 2026-08-11) and BOTH missed
things: a hostname with a space in it that the first pass didn't match, an
un-fuzzed distance between two named suburbs, a chip MAC hardcoded in a module
whose neighbour argues in prose that a chip MAC identifies a place and a person.

So these are PATTERN checks, not a list of the strings that leaked — a list
would republish exactly what it was written to remove, and would only ever
catch the leak that already happened.

They cover: home paths (a Mac home directory carries a real name), precise
coordinates (a 5-decimal position is a house, and this project's whole privacy
design rests on an 800 m fuzz), credentials, and personal email. Each check
names its own allowlist so an intentional exception is visible rather than
silent.
"""

import re
import subprocess

REPO_FILES = subprocess.run(["git", "ls-files"], capture_output=True,
                            text=True).stdout.split()


def _tracked_text():
    """(path, text) for every tracked text file."""
    for path in REPO_FILES:
        try:
            with open(path, encoding="utf-8") as f:
                yield path, f.read()
        except (UnicodeDecodeError, IsADirectoryError, FileNotFoundError):
            continue


def test_no_home_directory_paths():
    """/Users/<name> and /home/<name> carry a real person's name. The one on
    this machine reached the tree through a preview script's ROOT constant and
    survived until a history rewrite (2026-08-11)."""
    pat = re.compile(r"/(?:Users|home)/(?!\s)([A-Za-z0-9._-]+)")
    allowed = {"pi", "nodemedic", "everywhere", "runner", "root", "user",
               "USER", "ubuntu", "x", "tester"}   # fixture stand-ins
    leaks = []
    for path, text in _tracked_text():
        for m in pat.finditer(text):
            name = m.group(1)
            if name in allowed or name.startswith("$") or name.startswith("<"):
                continue
            line = text[:m.start()].count("\n") + 1
            leaks.append(f"{path}:{line}: {m.group(0)}")
    assert not leaks, "home directory paths name a person:\n" + "\n".join(leaks)


def test_no_house_precision_coordinates():
    """A latitude with five decimals is ~1 m: a dwelling, not a suburb.

    The operator's home sat in this history at seven decimals. City-level and
    obviously-synthetic points are fine — the rule is that anything precise
    must be declared synthetic where it is written.
    """
    pat = re.compile(r"-?\d{1,3}\.\d{5,}")
    #: Declared, deliberate, and checked by a human. Anything NOT here that is
    #: precise to five significant decimals fails — the point is that adding a
    #: house-precision number has to be a decision, not a paste.
    synthetic = (
        "-37.512345", "145.523456", "-37.5106", "145.5107", "-37.999999",
        "12.345678",                       # obvious stand-in in GPS fixtures
        "-37.810123", "144.962555",        # NMEA fixtures, city centre
        "85.05112878", "-85.05112878",     # the Web Mercator latitude limits
        "16.333333",                       # a UI ratio, not a place
        "0.000123", "0.000034",            # small magnitudes, not coordinates
    )
    leaks = []
    for path, text in _tracked_text():
        if path.endswith("test_privacy_tree.py"):
            continue
        for m in pat.finditer(text):
            value = m.group(0)
            if value in synthetic:
                continue
            # -37.814000 is a 3-decimal number written with padding: suburb
            # precision, not house precision. Count SIGNIFICANT decimals.
            if len(value.split(".")[1].rstrip("0")) < 5:
                continue
            # a bare number is only a coordinate if it could be one
            if not (-180.0 <= float(value) <= 180.0):
                continue
            line = text[:m.start()].count("\n") + 1
            leaks.append(f"{path}:{line}: {value}")
    assert not leaks, (
        "coordinates precise enough to be a house — fuzz them, round them, or "
        "add them to the declared synthetics:\n" + "\n".join(leaks))


def test_no_credentials():
    """GitHub tokens live in .claude/settings.local.json, which is gitignored —
    one `git add -f` from publication, in a repo whose .gitignore records that
    exactly that once happened."""
    pat = re.compile(r"ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
                     r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
    leaks = [path for path, text in _tracked_text()
             if pat.search(text) and not path.endswith("test_privacy_tree.py")]
    assert not leaks, f"credentials in tracked files: {leaks}"


def test_no_personal_email():
    """Commits are authored under a noreply address on purpose. A real one in a
    blob would undo that — 14 commits bearing one survived a rewrite by hanging
    off a tag nobody deleted."""
    pat = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    allowed_domains = ("users.noreply.github.com", "anthropic.com",
                       "example.com", "example.org", "domain.tld")
    # not addresses at all: a git remote, an mDNS host, a systemd template unit
    not_an_address = ("git@github.com",)
    leaks = []
    for path, text in _tracked_text():
        if path.endswith("test_privacy_tree.py"):
            continue
        for m in pat.finditer(text):
            addr = m.group(0)
            if addr.endswith(allowed_domains) or addr in not_an_address:
                continue
            if addr.endswith((".local", ".service", ".socket", ".target",
                              ".timer", ".mount", ".device")):
                continue
            line = text[:m.start()].count("\n") + 1
            leaks.append(f"{path}:{line}: {addr}")
    assert not leaks, "email addresses in tracked files:\n" + "\n".join(leaks)
