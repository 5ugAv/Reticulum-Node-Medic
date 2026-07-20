"""The 'still under construction' breadcrumb log — records unbuilt features people
actually hit so the developer can catch them between sessions."""

from ui import construction_log


def test_log_hit_and_read_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(construction_log, "LOG_PATH", str(tmp_path / "c.log"))
    construction_log.log_hit("Pi birth — under construction", "detail here",
                             note="please build this")
    construction_log.log_hit("Mitosis — under construction", "d2")
    hits = construction_log.read_hits(str(tmp_path / "c.log"))
    assert len(hits) == 2
    assert hits[0]["title"].startswith("Pi birth")
    assert hits[0]["note"] == "please build this"
    assert "t" in hits[0]


def test_log_hit_never_raises_on_bad_path(monkeypatch):
    monkeypatch.setattr(construction_log, "LOG_PATH", "/nonexistent\x00/c.log")
    construction_log.log_hit("x")            # must not raise
    assert construction_log.read_hits("/nope/nope.log") == []
