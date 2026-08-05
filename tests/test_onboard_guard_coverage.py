"""Every path that WRITES to a serial board must pass the onboard guard.

The medic carries its own permanent hardware — Jonesey (its LoRa radio) and its
GPS Tracker. Flashing one of them is unrecoverable in the field: the medic loses
the very radio it uses to see the mesh. Two real incidents shaped this file.

2026-07-22, THE NEAR-MISS. Birthing FAITH pointed a PlatformIO upload at
``ttyACM0`` — Jonesey — because ``detect_board`` did a naive ``ls /dev/ttyACM*``
and took ``ports[0]``. The write never landed only because the rnode-splitter
held the port busy. That was luck, not a safety.

2026-08-05, THIS AUDIT. Two live bypasses of the gate that was added afterwards:

  * ``workflows/robust_flash.py`` — the recovery ladder (write_flash,
    verify_flash, hard resets, and a uhubctl POWER CUT) had no guard at all, and
    ``rnode_v4_rgb._robust_rgb_overlay`` builds one directly from module scope,
    skipping that workflow's own class-level gate.
  * ``workflows/gps_setup.py`` — ``_ensure_single_board`` took ``ports[0]`` of
    every attached tty, with the ONLY protection being on-screen advice to "plug
    in ONLY the Tracker". Jonesey is permanently attached, so on a medic with
    nothing else plugged in there is exactly one candidate and it is the medic's
    own radio. Following the instruction was the way to destroy it.

Both were found by reading, not by a failing test — which is exactly why this
file exists. It fails when a NEW write site forgets the guard.
"""
import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

#: Commands that write, erase, or reset a board. Substring match on source text.
DESTRUCTIVE = (
    "write_flash", "erase_flash", "verify_flash",
    "arduino-cli upload", "--autoinstall", "dfu serial",
    "--target upload", "hard_reset",
)

#: Evidence the module actually RUNS a command rather than just building a
#: string. A pure command-builder needs no guard; the caller that executes does.
EXECUTES = (".run(", "subprocess.", "check_call", "Popen", "call(")

#: The guard, in any of its forms.
GUARDS = ("assert_flashable", "is_flashable_work_board", "local_board_ports",
          "_assert_work_board", "_is_candidate_work_board")

#: Reviewed exemptions. Each MUST carry a reason — an entry here is a claim that
#: the path cannot reach the medic's own hardware, not a way to silence a fail.
EXEMPT = {
    # Reads the chip id / MAC to IDENTIFY a board before anything is chosen.
    # It never writes, and it must run on a board we cannot yet name.
    "ui/board_detect.py": "read-only identification, no write",
    # Pure catalogue: builds command STRINGS for the caller to run. The caller
    # (rnode_flash.birth_flash) is guarded.
    "workflows/rnode_boards.py": "builds command strings, executes nothing",
    "workflows/updater.py": "builds command strings, executes nothing",
}


def _sources():
    for d in ("workflows", "ui", "provisioning", "transport", "monitor"):
        for p in (REPO / d).rglob("*.py"):
            yield p


def _rel(p):
    return str(p.relative_to(REPO))


def _docstrings(tree):
    """Every docstring node in *tree*, so prose about flashing isn't mistaken for
    flashing. Both false positives this detector first produced were modules that
    merely MENTION ``--autoinstall`` in a docstring."""
    out = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and \
                isinstance(body[0].value, ast.Constant) and \
                isinstance(body[0].value.value, str):
            out.add(id(body[0].value))
    return out


def _command_strings(text):
    """String literals that are real code, not docstrings. Comments never appear
    in the AST, so they are excluded for free."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    skip = _docstrings(tree)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in skip]


def _writes_to_a_board(text):
    strings = _command_strings(text)
    return (any(d in s for s in strings for d in DESTRUCTIVE)
            and any(e in text for e in EXECUTES))


def test_every_module_that_flashes_a_board_consults_the_guard():
    unguarded = []
    for path in _sources():
        rel = _rel(path)
        if rel in EXEMPT:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if not _writes_to_a_board(text):
            continue
        if not any(g in text for g in GUARDS):
            unguarded.append(rel)
    assert not unguarded, (
        "these modules run a destructive board command with no onboard guard — "
        "the medic's own radio could be the target:\n  " +
        "\n  ".join(sorted(unguarded)) +
        "\n\nAdd assert_flashable(port) immediately before the write, or add an "
        "entry to EXEMPT with a reason if the path provably cannot reach it."
    )


def test_the_exemptions_still_exist_and_still_do_not_write():
    """An exemption must not outlive the file it excuses, or quietly start
    writing. Both would turn a reviewed decision into a silent hole."""
    for rel, reason in EXEMPT.items():
        p = REPO / rel
        assert p.exists(), f"EXEMPT names {rel}, which no longer exists"
        assert reason.strip(), f"{rel} is exempt with no reason given"


def test_the_detector_would_actually_catch_a_regression():
    """Guards the guard: if DESTRUCTIVE/EXECUTES stopped matching real code, the
    coverage test above would pass vacuously and prove nothing."""
    fake = "self.c.run(f'{esptool} --port {port} write_flash 0x0 {path}')"
    assert _writes_to_a_board(fake)
    assert not _writes_to_a_board("return f'esptool --port {port} write_flash'")


# -- the two specific holes this audit closed --------------------------------

def test_robust_flasher_refuses_the_medics_own_radio(monkeypatch):
    """The ladder must refuse in the CONSTRUCTOR, so it never begins — it
    power-cycles USB and writes in chunks, and a half-run ladder on the medic's
    own radio is the worst outcome."""
    import workflows.robust_flash as rf
    from ui.onboard_roster import ProtectedBoardError

    monkeypatch.setattr(rf, "_assert_work_board",
                        lambda port: (_ for _ in ()).throw(
                            ProtectedBoardError("that is Jonesey")))
    with pytest.raises(ProtectedBoardError):
        rf.RobustFlasher(object(), "/dev/ttyACM0")


def test_gps_setup_never_offers_an_onboard_board_as_the_tracker(monkeypatch):
    """The original bug: one attached port, and it was Jonesey."""
    import workflows.gps_setup as g

    monkeypatch.setattr(g, "_serial_ports", lambda c: ["/dev/ttyACM0"])
    monkeypatch.setattr(g, "_is_candidate_work_board", lambda p: False)
    s = g.GpsTrackerSetup(object())
    s.port = None
    res = s._ensure_single_board()
    assert res.success is False
    assert s.port is None, "an onboard board was selected as the flash target"


def test_gps_flash_refuses_even_when_handed_the_port_explicitly(monkeypatch):
    """Belt and braces: a caller that sets .port directly skips selection, so
    the write boundary itself has to say no."""
    import workflows.gps_setup as g
    from ui.onboard_roster import ProtectedBoardError

    monkeypatch.setattr(g, "_assert_work_board",
                        lambda port: (_ for _ in ()).throw(
                            ProtectedBoardError("that is Jonesey")))

    class Boom:
        def run(self, *a, **k):
            raise AssertionError("upload ran against a protected board")

    s = g.GpsTrackerSetup(Boom())
    s.port = "/dev/ttyACM0"
    res = s._flash()
    assert res.success is False
    assert "refusing" in res.message.lower()
