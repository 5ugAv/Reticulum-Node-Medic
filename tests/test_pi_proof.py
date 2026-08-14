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
    proven, imposter = imaged_pi_answers(
        "everywhere", path=path,
        _token_at=lambda addr: "tok-1234",
        _cable=lambda: "10.55.0.1", _resolve=lambda h: None)
    assert proven is True and imposter == ""


def test_an_answering_machine_with_the_wrong_token_is_an_imposter(tmp_path):
    """The 2026-08-14 morning, replayed: the OLD node answers the name."""
    path = _record(tmp_path)
    proven, imposter = imaged_pi_answers(
        "everywhere", path=path,
        _token_at=lambda addr: "someone-elses-token",
        _cable=lambda: "", _resolve=lambda h: "192.168.1.42")
    assert proven is False
    assert imposter == "192.168.1.42"


def test_silence_is_neither_proof_nor_imposter(tmp_path):
    path = _record(tmp_path)
    proven, imposter = imaged_pi_answers(
        "everywhere", path=path,
        _token_at=lambda addr: None,          # nobody answers ssh
        _cable=lambda: "", _resolve=lambda h: None)
    assert (proven, imposter) == (False, "")


def test_a_token_for_a_DIFFERENT_card_proves_nothing(tmp_path):
    """The record is scoped to the hostname it was written for — a stray
    record from another node's imaging must not gate THIS birth."""
    path = _record(tmp_path, hostname="hope", token="hopes-token")
    proven, imposter = imaged_pi_answers(
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
    proven, _ = imaged_pi_answers(
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
    assert "_pi_proven" in watcher and "_pi_answering()" not in watcher


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
    assert "birth token" in body or "proves itself" in body, (
        "the step should say WHY the screen moves for the right Pi only")
