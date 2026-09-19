"""Board coverage — DONE derives itself from the cert ledger, so it can never
rot as work gets done (operator, 2026-09-18: "the todo lists are gonna get
done, so the list will need to be adjusted as we move along").

Only INTENT is hand-declared. TODO = intent minus what the ledger proves.
Birth a board, regenerate, and it leaves the todo by itself."""
from provisioning.board_coverage import (classify_cert, coverage,
                                         FIRMWARE_KIND)


def test_firmware_version_tells_rnode_from_rtnode():
    # 0.x = RTNode-2400 firmware line; 1.8x = RNode firmware line.
    assert FIRMWARE_KIND("0.7.0") == "rtnode2400"
    assert FIRMWARE_KIND("1.85") == "rnode"
    assert FIRMWARE_KIND(None) == "unknown"


def test_a_pi_build_is_classified_by_its_role_not_its_board():
    c = {"node_name": "SKYFINGER", "board": "Heltec LoRa32 v4",
         "role": "LXMF propagation node"}
    k = classify_cert(c)
    assert k.kind == "pi_rnode" and k.board == "Heltec LoRa32 v4"


def test_a_bare_rnode_is_an_rnode():
    assert classify_cert({"board": "RAK4631", "node_type": "rnode"}).kind == "rnode"


def test_an_rtnode_is_read_from_its_firmware_line():
    c = {"node_name": "RTNode 5AA9", "board": "Ebyte EoRa-S3", "firmware": "0.7.0"}
    assert classify_cert(c).kind == "rtnode2400"


def test_the_t114_at_1_85_is_AMBIGUOUS_not_assumed_a_modem():
    """CORRECTED 2026-09-19, by the board itself. This test used to assert
    "rnode" — because 1.85 looked like the RNode version line. Then a T114
    was born as a genuine RTNode-2400 and reported 1.85 too: the nRF52
    RTNode image is built FROM the RNode firmware source. The version is
    blind on these boards, so an old cert that does not state its node_type
    is honestly unknown. Guessing "modem" would have written a falsehood
    into the coverage doc, which exists to be trusted."""
    c = {"node_name": "t115", "board": "Heltec Mesh Node T114", "firmware": "1.85"}
    assert classify_cert(c).kind == "unknown"


def test_a_cert_that_states_its_node_type_is_believed_over_any_version():
    """Certs written from 2026-09-19 stamp what the build actually made."""
    c = {"node_name": "RTnodet114", "board": "Heltec Mesh Node T114",
         "firmware": "1.85", "node_type": "rtnode2400"}
    assert classify_cert(c).kind == "rtnode2400"


def test_an_esp32_at_1_85_is_still_a_modem():
    """The version DOES decide on the ESP32 targets — their RTNode images
    carry the 0.x line, so 1.85 there means a stock RNode flash."""
    c = {"board": "Heltec LoRa32 v4", "firmware": "1.85"}
    assert classify_cert(c).kind == "rnode"


def test_coverage_marks_intent_done_only_when_the_ledger_proves_it():
    certs = [
        {"board": "LilyGO T-Echo", "firmware": "0.7.0"},          # rtnode proven
        {"board": "Heltec LoRa32 v4", "firmware": "1.85"},        # modem only
    ]
    intent = [("Heltec LoRa32 v4", "rtnode2400"),
              ("LilyGO T-Echo", "rtnode2400")]
    rows = coverage(certs, intent)
    by = {(r.board, r.kind): r for r in rows}
    assert by[("LilyGO T-Echo", "rtnode2400")].done is True
    assert by[("Heltec LoRa32 v4", "rtnode2400")].done is False
    # ...and its modem proof is surfaced as context, not counted as the goal
    assert "rnode" in by[("Heltec LoRa32 v4", "rtnode2400")].note.lower()


def test_todo_shrinks_when_the_ledger_grows():
    """The property the operator asked for: prove the board, and it leaves the
    todo on the next regeneration — no hand-editing."""
    intent = [("Heltec Mesh Node T114", "rtnode2400")]
    before = coverage([], intent)
    assert before[0].done is False
    after = coverage(
        [{"board": "Heltec Mesh Node T114", "firmware": "0.7.0"}], intent)
    assert after[0].done is True
