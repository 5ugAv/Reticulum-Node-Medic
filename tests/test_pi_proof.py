"""The connect-Pi step advances on PROOF, not presence.

Field failure, 2026-08-14, EVERYWHERE rebirth: the operator reached "Move the
card to the Raspberry Pi", and the guide jumped straight to "Bring the node
to life" — the instruction screen between them was skipped. The old
EVERYWHERE, still cabled to the medic from the night before, answered the
presence probe, and _step_is_redundant took ANY answering Pi as THE Pi.
During a REBIRTH the machine being replaced is nearly always still around —
presence proves nothing. The card's baked birth token does: only the machine
booted from the card the medic just wrote can quote it.

Same law as detect_hardware's token check (operator's four-point critique),
applied one screen earlier — the walkthrough must not even LOOK past the
connect step until the right Pi answers.
"""
from provisioning.pi_discover import imaged_pi_answers


def _record(tmp_path, hostname="everywhere", token="tok-1234"):
    import json
    p = tmp_path / "last_imaged_pi.json"
    p.write_text(json.dumps({"hostname": hostname, "birth_token": token}))
    return str(p)


def test_proven_when_the_imaged_card_answers_with_its_token(tmp_path):
    path = _record(tmp_path)
    proven, imposter, why = imaged_pi_answers(
        "everywhere", path=path,
        _token_at=lambda addr: "tok-1234",
        _cable=lambda: "10.55.0.1", _resolve=lambda h: None)
    assert (proven, imposter, why) == (True, "", "")


def test_an_answering_machine_with_the_wrong_token_is_an_imposter(tmp_path):
    """The 2026-08-14 morning, replayed: the OLD node answers the name."""
    path = _record(tmp_path)
    proven, imposter, why = imaged_pi_answers(
        "everywhere", path=path,
        _token_at=lambda addr: "someone-elses-token",
        _cable=lambda: "", _resolve=lambda h: "192.168.1.42")
    assert proven is False
    assert imposter == "192.168.1.42"
    assert why == "wrong-token"


def test_silence_is_neither_proof_nor_imposter(tmp_path):
    path = _record(tmp_path)
    proven, imposter, why = imaged_pi_answers(
        "everywhere", path=path,
        _token_at=lambda addr: None,          # nobody answers ssh
        _cable=lambda: "", _resolve=lambda h: None)
    assert (proven, imposter, why) == (False, "", "")


def test_a_token_for_a_DIFFERENT_card_proves_nothing(tmp_path):
    """The record is scoped to the hostname it was written for — a stray
    record from another node's imaging must not gate THIS birth."""
    path = _record(tmp_path, hostname="hope", token="hopes-token")
    proven, imposter, why = imaged_pi_answers(
        "everywhere", path=path,
        _token_at=lambda addr: "hopes-token",
        _cable=lambda: "10.55.0.1", _resolve=lambda h: None)
    assert proven is False


def test_the_cable_road_wins_but_both_roads_are_walked(tmp_path):
    path = _record(tmp_path)
    asked = []
    def token_at(addr):
        asked.append(addr)
        return "tok-1234" if addr == "192.168.1.50" else "wrong"
    proven, _imp, _why = imaged_pi_answers(
        "everywhere", path=path, _token_at=token_at,
        _cable=lambda: "10.55.0.1", _resolve=lambda h: "192.168.1.50")
    assert proven is True
    assert asked == ["10.55.0.1", "192.168.1.50"]


# -- the screen is wired to the proof, not the presence probe --------------

def test_the_skip_and_the_watcher_require_proof():
    from tests.srcutil import func_source
    skip = func_source("ui/screens/birth_guide_screen.py",
                       "_step_is_redundant", cls="BirthGuideScreen")
    assert "_pi_proven" in skip
    assert "self._pi_answering()" not in skip, (
        "the forward skip judged ANY answering Pi as THE Pi — that is the "
        "skipped-instruction bug of 2026-08-14")
    watcher = func_source("ui/screens/birth_guide_screen.py",
                          "_start_pi_poll", cls="BirthGuideScreen")
    assert "_pi_proof" in watcher and "_pi_answering()" not in watcher


def test_the_connect_step_tells_the_truth_about_both_roads():
    """Operator, 2026-08-14: 'maybe it doesn't need to be connected by
    Wi-Fi... let me know which is correct.' Both roads work — the card is
    baked with the Wi-Fi credentials AND the cable needs no network — so
    the step must say both, not command the cable as if it were the only way."""
    from ui.birth_guide_flow import guide_steps
    step = next(s for s in guide_steps("pi") if s.get("anim") == "connect_pi")
    body = (step["title"] + " " + step.get("body", "")).lower()
    assert "wi-fi" in body or "wifi" in body
    assert "cable" in body
    # (the WHY — the birth-token proof — moved to the machinery itself;
    # the screen keeps its words short enough for the 5-inch panel)


# -- the card writer must be THE card writer (2026-08-14, skyfinger) -------
# The installed root helper (/usr/local/lib/nodemedic/prepare-card) predated
# the birth-token bake, so every card it wrote carried no token — and the
# connect-Pi step then correctly refused to advance on an unprovable machine.
# The stall was honest; the card was wrong. Sudoers only lets the helper be
# updated with the real password, so the imager REFUSES to write while the
# installed copy differs from the repo's — a stale root helper is a mute-node
# factory, and refusing loudly beats writing quietly.

def test_matching_helper_is_silent(tmp_path):
    from provisioning.pi_imager import helper_out_of_date
    a = tmp_path / "installed"; b = tmp_path / "repo"
    a.write_text("same bytes"); b.write_text("same bytes")
    assert helper_out_of_date(installed=str(a), repo_copy=str(b)) == ""


def test_stale_helper_is_named_with_the_fix(tmp_path):
    from provisioning.pi_imager import helper_out_of_date
    a = tmp_path / "installed"; b = tmp_path / "repo"
    a.write_text("old"); b.write_text("new")
    why = helper_out_of_date(installed=str(a), repo_copy=str(b))
    assert "out of date" in why.lower()
    assert "sudo install" in why, "say the fix, not just the fault"


def test_unreadable_helper_is_could_not_check_not_a_pass(tmp_path):
    from provisioning.pi_imager import helper_out_of_date
    b = tmp_path / "repo"; b.write_text("new")
    why = helper_out_of_date(installed=str(tmp_path / "missing"),
                             repo_copy=str(b))
    assert why, "an unreadable root helper must not pass silently"


def test_the_imager_screen_refuses_on_a_stale_helper():
    from tests.srcutil import func_source
    confirm = func_source("ui/screens/pi_imager_screen.py", "_confirm",
                          cls="PiImagerScreen")
    assert "helper_out_of_date" in confirm


# -- the watcher narrates what it sees (operator, 2026-08-14: "Do that now") --

def test_a_no_token_machine_is_reported_on_the_glass():
    from tests.srcutil import func_source
    watcher = func_source("ui/screens/birth_guide_screen.py",
                          "_start_pi_poll", cls="BirthGuideScreen")
    assert "answered without a" in watcher and "re-image" in watcher, (
        "the skyfinger stall, named on screen — calmly (briefing Task 3)")
    assert "Ignoring another node" in watcher
    assert "set_status" in watcher


def test_the_empty_and_wrong_token_cases_are_told_apart(tmp_path):
    from provisioning.pi_discover import imaged_pi_answers
    path = _record(tmp_path)
    _p, _i, why_empty = imaged_pi_answers(
        "everywhere", path=path, _token_at=lambda a: "",
        _cable=lambda: "10.55.0.1", _resolve=lambda h: None)
    assert why_empty == "no-token"


# -- token-authenticated key rotation (Task 0, 2026-08-14 night) -----------
# Both SKYFINGER runs died at detect_hardware: the PINNED store held
# 192.168.1.2 from ELSEWHERE's build, DHCP gave skyfinger that address, and
# the honest changed-key refusal blocked a legitimate rebirth. The rule
# stands — never drop pinned trust on name resolution — but the birth token
# is not name resolution: the medic minted it, baked it onto this card, and
# recorded it once. A machine that quotes it back IS the card the medic
# wrote, and that proof outranks a stale address pin.

def test_changed_key_with_matching_token_rotates_the_pin(tmp_path):
    from workflows.build import _rotate_pin_on_token_proof
    calls = []
    ok = _rotate_pin_on_token_proof(
        "192.168.1.2", "tok-99",
        _read_token=lambda addr: "tok-99",
        _unpin=lambda h: calls.append(("unpin", h)) or True,
        _clear_user=lambda h: calls.append(("clear", h)) or True)
    assert ok is True
    assert ("unpin", "192.168.1.2") in calls
    assert ("clear", "192.168.1.2") in calls


def test_wrong_or_absent_token_leaves_the_pin(tmp_path):
    from workflows.build import _rotate_pin_on_token_proof
    for answer in ("different-token", "", None):
        calls = []
        ok = _rotate_pin_on_token_proof(
            "192.168.1.2", "tok-99",
            _read_token=lambda addr, a=answer: a,
            _unpin=lambda h: calls.append("unpin") or True,
            _clear_user=lambda h: calls.append("clear") or True)
        assert ok is False, f"rotated on token answer {answer!r}"
        assert "unpin" not in calls, "the pin fell without proof"


def test_no_expected_token_never_rotates():
    from workflows.build import _rotate_pin_on_token_proof
    ok = _rotate_pin_on_token_proof(
        "192.168.1.2", "",
        _read_token=lambda addr: "anything",
        _unpin=lambda h: True, _clear_user=lambda h: True)
    assert ok is False


def test_detect_hardware_tries_the_rotation_on_changed_key():
    from tests.srcutil import func_source
    detect = func_source("workflows/build.py", "detect_hardware")
    assert "_rotate_pin_on_token_proof" in detect
