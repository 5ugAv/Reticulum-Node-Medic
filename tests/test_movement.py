"""MovementDetector — auto-backpack-when-moving core (pure, no Kivy/GPS)."""

from monitor.movement import MovementDetector, haversine_m


# A ~1 m step in latitude at mid-southern latitudes; used to fabricate
# small/large hops. START is a synthetic point, not anyone's home.
_DEG_PER_M_LAT = 1.0 / 111_320.0
START = (-37.5106, 145.5107)


def _north(base, metres):
    return (base[0] + metres * _DEG_PER_M_LAT, base[1])


def test_haversine_known_distance():
    d = haversine_m(START[0], START[1], *_north(START, 100))
    assert 98 <= d <= 102          # ~100 m north


def test_first_fix_just_settles_no_movement():
    det = MovementDetector()
    assert det.update(*START) is False
    assert det.anchor == START


def test_stationary_jitter_never_trips():
    det = MovementDetector(threshold_m=150.0)
    det.update(*START)
    for m in (5, -8, 12, -3, 10, -15):     # GPS breathing within ~15 m
        assert det.update(*_north(START, m)) is False


def test_needs_confirmation_before_firing():
    det = MovementDetector(threshold_m=150.0, confirm=2)
    det.update(*START)
    assert det.update(*_north(START, 300)) is False   # 1st over-threshold: not yet
    assert det.update(*_north(START, 320)) is True     # 2nd: confirmed movement


def test_single_wild_fix_then_back_does_not_fire():
    det = MovementDetector(threshold_m=150.0, confirm=2)
    det.update(*START)
    assert det.update(*_north(START, 400)) is False   # one wild fix
    assert det.update(*_north(START, 5)) is False      # back home — counter reset
    assert det._over == 0


def test_reanchors_after_firing_so_it_does_not_spam():
    det = MovementDetector(threshold_m=150.0, confirm=1)
    det.update(*START)
    assert det.update(*_north(START, 200)) is True     # fires, re-anchors at 200 m
    assert det.update(*_north(START, 210)) is False    # small step from new anchor
    assert det.update(*_north(START, 400)) is True     # another 190 m on → fires


def test_cumulative_small_hops_still_trip():
    # None of these hops individually clears 150 m from the PREVIOUS fix, but they
    # add up from the anchor — you can't creep the unit away unnoticed.
    det = MovementDetector(threshold_m=150.0, confirm=1)
    det.update(*START)
    assert det.update(*_north(START, 100)) is False
    assert det.update(*_north(START, 160)) is True     # 160 m from the anchor


def test_reset_forgets_anchor():
    det = MovementDetector()
    det.update(*START)
    det.reset()
    assert det.anchor is None
    assert det.update(*_north(START, 500)) is False    # re-settles, doesn't fire


def test_reset_to_point_measures_from_there():
    det = MovementDetector(threshold_m=150.0, confirm=1)
    far = _north(START, 1000)
    det.reset(*far)
    assert det.anchor == far
    assert det.update(*_north(far, 50)) is False      # near the new anchor
    assert det.update(*_north(far, 200)) is True


def test_none_fix_is_ignored():
    det = MovementDetector()
    assert det.update(None, None) is False
    assert det.anchor is None
