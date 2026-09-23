"""The medic's side of time over the mesh, pinned in ui/app.py
(docs/HEALTH_REPLY_UNICAST.md, "Time over the mesh", 2026-09-23): the
reply-destination callback dispatches BEFORE it verifies — a 25-byte 0x06
is a TIME_REQ, a 98-byte 0x07 whose nonce is pending is a TIME_ACK, and
everything else is the health reply it always was; a TIME is sent only
after the node's identity is recalled and a path warmed, signed by the
medic's own reply identity; an ack is verified under the node's key and
recorded in the ledger; and a TIME is pushed unasked after a verified
reply when none went to that node in six hours."""
from tests.srcutil import func_source

APP = "ui/app.py"
CLS = "ReticulumNodeMedicApp"


def test_the_callback_dispatches_before_it_verifies():
    body = func_source(APP, "_on_health_reply", cls=CLS)
    assert "classify_inbound(" in body
    assert body.index("classify_inbound(") < body.index("verify_reply(")
    assert '"time_req"' in body and "_on_time_req(" in body
    assert '"time_ack"' in body and "_on_time_ack(" in body
    assert "_pending_times.is_pending" in body
    # a verified reply is followed by the unasked push, after ingest
    assert body.index('source="reply"') < body.index("_maybe_push_time(")


def test_a_time_is_signed_by_the_reply_identity_after_recall_and_a_warm_path():
    body = func_source(APP, "_send_time", cls=CLS)
    assert "RNS.Identity.recall(" in body
    assert "warm_path(" in body and "request_path" in body
    assert "build_time(" in body and "self._reply_ident.sign" in body
    assert '"rtnode", "health"' in body
    assert "_pending_times.add(" in body and "_time_ledger.record_sent(" in body
    # the medic's own clock must be sane before it is offered to anyone
    assert "epoch_is_sane(" in body
    assert "_time_log(" in body
    log = func_source(APP, "_time_log", cls=CLS)
    assert "[time]" in log and "flush=True" in log and "LOG_NOTICE" in log


def test_an_ack_is_verified_under_the_nodes_key_and_recorded():
    body = func_source(APP, "_on_time_ack", cls=CLS)
    assert "verify_time_ack(" in body and "RNS.Identity.recall" in body
    assert "_pending_times.claim(" in body
    assert "_time_ledger.record_ack(" in body


def test_the_unasked_push_keeps_the_six_hour_cadence():
    body = func_source(APP, "_maybe_push_time", cls=CLS)
    assert "_time_ledger.should_push(" in body
    assert '"push"' in body
    src = open(APP).read()
    assert "TIME_PUSH_EVERY_S" in src


def test_the_request_is_answered_off_the_inbound_thread():
    body = func_source(APP, "_on_time_req", cls=CLS)
    assert "threading.Thread(" in body and "_send_time" in body
