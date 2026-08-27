"""Per-link SNR margin attribution — every refusal here is an honesty rule:
no packet since the send, stale state, clock nonsense, unbelievable numbers
-> None, never a stale or fabricated reading."""

import json

from monitor.link_margin import (LinkMargin, SF9_SNR_LIMIT, link_margin)


def shm(tmp_path, **kw):
    p = tmp_path / "state.json"
    base = {"packet_heard_at": 1000.0, "last_rssi": -92, "last_snr": 8.5,
            "noise_floor": -105}
    base.update(kw)
    p.write_text(json.dumps(base))
    return str(p)


def test_reply_after_send_is_attributed(tmp_path):
    lm = link_margin(sent_at=999.0, path=shm(tmp_path), now=lambda: 1001.0)
    assert lm is not None
    assert lm.rssi_dbm == -92 and lm.snr_db == 8.5
    assert lm.over_floor_db == 13
    assert lm.headroom_db == round(8.5 - SF9_SNR_LIMIT, 1)
    assert lm.verdict == "strong"


def test_a_packet_from_before_the_send_is_refused(tmp_path):
    # the whole point: never attribute an OLD packet to this node's reply
    assert link_margin(sent_at=1000.5, path=shm(tmp_path),
                       now=lambda: 1002.0) is None


def test_missing_state_or_fields_refuse(tmp_path):
    assert link_margin(sent_at=1.0, path=str(tmp_path / "nope.json")) is None
    assert link_margin(sent_at=1.0, path=shm(tmp_path, last_rssi=None),
                       now=lambda: 1001.0) is None


def test_clock_nonsense_refuses(tmp_path):
    assert link_margin(sent_at=999.0, path=shm(tmp_path),
                       now=lambda: 900.0) is None    # heard "in the future"


def test_unbelievable_rssi_refuses_and_bad_floor_is_dropped(tmp_path):
    assert link_margin(sent_at=999.0, path=shm(tmp_path, last_rssi=-5),
                       now=lambda: 1001.0) is None
    lm = link_margin(sent_at=999.0, path=shm(tmp_path, noise_floor=-20),
                     now=lambda: 1001.0)
    assert lm is not None and lm.floor_dbm is None and lm.over_floor_db is None


def test_verdict_ladder():
    def lm(snr):
        return LinkMargin(rssi_dbm=-100, snr_db=snr, floor_dbm=-105,
                          heard_at=0.0)
    assert lm(10.0).verdict == "strong"
    assert lm(3.0).verdict == "ok"
    assert lm(-3.0).verdict == "thin"
    assert lm(-9.0).verdict == "edge"


def test_no_snr_falls_back_to_over_floor():
    lm = LinkMargin(rssi_dbm=-85, snr_db=None, floor_dbm=-105, heard_at=0.0)
    assert lm.verdict == "strong"          # 20 dB over floor
    lm = LinkMargin(rssi_dbm=-104, snr_db=None, floor_dbm=-105, heard_at=0.0)
    assert lm.verdict in ("thin", "edge")


def test_ping_answered_report_carries_the_margin():
    # source pin: the ANSWERED branch attributes the reply's RF via
    # link_margin(sent_at) and never fabricates when it returns None.
    src = open("ui/app.py").read()
    assert "from monitor.link_margin import link_margin" in src
    assert "link_margin(sent_at[0])" in src
    assert "Signal as heard by the medic" in src
