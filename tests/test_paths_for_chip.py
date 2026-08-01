"""Only offer builds the connected board can actually do.

Operator, 2026-08-02: "after the board's been read we know this board can't be
an RTNode-2400 — remove that selection." firmware_options() already encoded the
rule; the chooser simply wasn't asking it, so the operator was offered a build
that would fail later.
"""

from ui.birth_guide_flow import paths_for_chip, BIRTH_PATHS

_S3_ONLY = lambda chip: chip == "esp32s3"       # noqa: E731


def _keys(paths):
    return [p[0] for p in paths]


def test_a_classic_esp32_is_not_offered_the_rtnode_build():
    """LoRa32 / T-Beam / Heltec V2 are classic ESP32 — RTNode-2400 needs an S3."""
    paths, why = paths_for_chip("esp32", _S3_ONLY)
    assert "radio" not in _keys(paths)
    assert why, "must explain the absence, not just vanish"
    assert set(_keys(paths)) == {"host", "pi"}


def test_an_s3_keeps_every_option():
    paths, why = paths_for_chip("esp32s3", _S3_ONLY)
    assert _keys(paths) == _keys(BIRTH_PATHS)
    assert why == ""


def test_an_unreadable_chip_offers_everything():
    """Fail OPEN. A wrong exclusion blocks a build the operator wants, which is
    worse than one extra choice."""
    for chip in (None, ""):
        paths, why = paths_for_chip(chip, _S3_ONLY)
        assert _keys(paths) == _keys(BIRTH_PATHS)
        assert why == ""


def test_the_rnode_and_pi_paths_are_never_removed():
    """Every board can be an RNode, and the Pi path doesn't depend on the
    attached radio's chip at all."""
    for chip in ("esp32", "esp32s3", "nrf52", None):
        paths, _ = paths_for_chip(chip, _S3_ONLY)
        assert "host" in _keys(paths) and "pi" in _keys(paths)


def test_order_is_preserved():
    """The deliberate rising-complexity order must survive filtering."""
    paths, _ = paths_for_chip("esp32", _S3_ONLY)
    assert _keys(paths) == [k for k in _keys(BIRTH_PATHS) if k != "radio"]
