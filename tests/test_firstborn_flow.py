"""The firstborn ceremony's stage logic (kivy-free)."""

from ui import firstborn_flow as ff


def test_no_tracker_asks_for_it_and_offers_no_begin():
    v = ff.decide(gps_live=False, tracker_candidates=0)
    assert v.stage == ff.NEED_TRACKER
    assert not v.can_begin


def test_two_candidates_refuse_to_guess():
    # the medic's own RNode is a look-alike; never flash a guess
    v = ff.decide(gps_live=False, tracker_candidates=2)
    assert v.stage == ff.NEED_TRACKER
    assert not v.can_begin


def test_one_tracker_no_gps_is_ready():
    v = ff.decide(gps_live=False, tracker_candidates=1)
    assert v.stage == ff.READY
    assert v.can_begin


def test_existing_gps_steps_aside_rather_than_reflash():
    v = ff.decide(gps_live=True, tracker_candidates=1)
    assert v.stage == ff.ALREADY
    assert not v.can_begin


def test_running_outranks_plug_state():
    # a mid-flash USB re-enumeration must not snap the screen back to "plug in"
    v = ff.decide(gps_live=False, tracker_candidates=0, running=True)
    assert v.stage == ff.BIRTHING
    assert not v.can_begin


def test_success_celebrates():
    v = ff.decide(gps_live=False, tracker_candidates=1, result=True)
    assert v.stage == ff.DONE
    assert v.celebrate is True


def test_only_done_celebrates():
    for v in (ff.decide(False, 1), ff.decide(True, 1),
              ff.decide(False, 0), ff.decide(False, 1, running=True),
              ff.decide(False, 1, result=False)):
        assert v.celebrate is False


def test_failure_names_the_reason_and_lets_you_retry():
    v = ff.decide(gps_live=False, tracker_candidates=1, result=False,
                  failure="Could not see satellites in time.")
    assert v.stage == ff.FAILED
    assert "satellites" in v.body
    assert v.can_begin           # try again


def test_result_outranks_gps_live_so_the_celebration_holds():
    # after a successful birth gpsd is now live; the screen must still show the
    # celebration, not flip to ALREADY.
    v = ff.decide(gps_live=True, tracker_candidates=1, result=True)
    assert v.stage == ff.DONE


# -- success/failure extraction from GpsTrackerSetup.run_all's LIST result ----

class _R:
    def __init__(self, success, message=""):
        self.success = success
        self.message = message


def test_succeeded_needs_every_step_to_pass():
    assert ff.succeeded([_R(True), _R(True)]) is True
    assert ff.succeeded([_R(True), _R(False)]) is False


def test_a_nonempty_list_is_not_itself_a_win():
    # the trap: run_all returns a non-empty list even when it FAILED early
    assert ff.succeeded([_R(False, "flash failed")]) is False


def test_succeeded_is_false_on_empty_run():
    assert ff.succeeded([]) is False
    assert ff.succeeded(None) is False


def test_first_failure_names_the_failed_step():
    msg = ff.first_failure([_R(True, "flashed"), _R(False, "no satellites")])
    assert msg == "no satellites"


def test_first_failure_default_when_all_passed():
    assert "didn't finish" in ff.first_failure([_R(True)])
