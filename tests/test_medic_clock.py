"""The medic signs only a DISCIPLINED clock (docs/HEALTH_REPLY_UNICAST.md,
"Time over the mesh", the disciplined-clock rule, 2026-09-23): a Pi 5 medic
with no RTC battery boots to a bogus time after a power loss, and a
signature would tell every node not to argue with it."""
from monitor.medic_clock import MAX_GPS_AGE_S, disciplined

NOW = 1_790_000_000.0


def test_a_recent_gps_discipline_vouches_for_the_clock():
    ok, why = disciplined(NOW, NOW - 600, ntp_synced=False)
    assert ok and "GPS" in why and "10 min" in why
    ok, why = disciplined(NOW, NOW - MAX_GPS_AGE_S, ntp_synced=None)
    assert ok
    assert MAX_GPS_AGE_S == 6 * 3600


def test_ntp_synchronised_vouches_when_gps_does_not():
    ok, why = disciplined(NOW, None, ntp_synced=True)
    assert ok and "NTP" in why
    ok, why = disciplined(NOW, NOW - MAX_GPS_AGE_S - 1, ntp_synced=True)
    assert ok and "NTP" in why


def test_neither_refuses_and_names_what_was_missing():
    ok, why = disciplined(NOW, None, ntp_synced=False)
    assert not ok and "no GPS discipline" in why and "NTP is not synchronised" in why
    ok, why = disciplined(NOW, None, ntp_synced=None)
    assert not ok and "could not be read" in why
    ok, why = disciplined(NOW, NOW - MAX_GPS_AGE_S - 1, ntp_synced=False)
    assert not ok and "older than" in why and "6.0 h" in why
    # a discipline stamped in the future means the clock moved backwards since
    ok, why = disciplined(NOW, NOW + 100, ntp_synced=False)
    assert not ok and "future" in why


def test_garbage_inputs_refuse_rather_than_raise():
    assert disciplined("soon", None, True)[0] is False
    assert disciplined(NOW, "x", False)[0] is False
    ok, _ = disciplined(NOW, "x", True)
    assert ok, "NTP still counts when the GPS stamp is unreadable"
