import pytest

from monitor.health_beacon import encode, decode
from monitor.health_poll import PollResult
from monitor.http_status import NodeStatus
from monitor.registry import NodeRegistry, NodeRecord, STALE_ALERT_HOURS


def http(status="ok", reachable=True, name="MEDIC-TEST", fw="0.6.2", faults=None):
    return NodeStatus(reachable=reachable, status=status, node_name=name,
                      firmware_version=fw, lora_online=True,
                      local_tcp_server_up=True, faults=faults or [])


def test_record_http_status_registers_and_adopts_name():
    reg = NodeRegistry()
    rec = reg.record_http_status(HASH, http(name="MEDIC-TEST"), NOW)
    assert rec.name == "MEDIC-TEST"
    assert rec.last_seen == NOW
    assert rec.status(NOW) == "ok"
    assert rec.firmware_version == "0.6.2"


def test_http_status_drives_traffic_light():
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(status="alert", faults=["undervoltage"]), NOW)
    assert reg.get(HASH).status(NOW) == "alert"


def test_http_preferred_over_beacon_when_reachable():
    reg = NodeRegistry()
    reg.ingest(HASH, beacon(fault=True), NOW)          # beacon says alert
    reg.record_http_status(HASH, http(status="ok"), NOW)   # but HTTP says ok
    assert reg.get(HASH).status(NOW) == "ok"


def test_unreachable_http_does_not_refresh_last_seen():
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(), NOW)
    reg.record_http_status(HASH, http(reachable=False, status="unreachable"),
                           NOW + HOUR)
    # last_seen stayed at NOW -> staleness governs, not the failed poll
    assert reg.get(HASH).last_seen == NOW


def test_http_node_goes_stale_to_alert():
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(), NOW)
    assert reg.get(HASH).status(NOW + (STALE_ALERT_HOURS + 1) * HOUR) == "alert"


def test_to_dashboard_dict_shape_for_screen():
    reg = NodeRegistry()
    reg.register(HASH, name="MEDIC-TEST", location="Bench", node_type="rtnode2400")
    reg.record_http_status(HASH, http(name="MEDIC-TEST"), NOW)
    reg.get(HASH).latest_http.wifi_rssi_dbm = -64
    d = reg.get(HASH).to_dashboard(NOW)
    assert d["name"] == "MEDIC-TEST"
    assert d["location"] == "Bench"
    assert d["status"] == "ok"
    assert d["type"] == "rtnode2400"
    assert d["signal_dbm"] == -64
    assert d["last_seen_hours"] == 0.0


def test_to_dashboard_signal_falls_back_to_beacon():
    reg = NodeRegistry()
    reg.ingest(HASH, beacon(wifi_rssi_dbm=-70), NOW)
    assert reg.get(HASH).to_dashboard(NOW)["signal_dbm"] == -70


def test_to_dashboard_carries_a_fresh_echo_without_touching_status():
    """The display side of 2026-08-21: replays kept a dead board's row green.
    The echo age now rides to the screen — but as its own key, muted there,
    and the status the dict carries is the one staleness computed, never one
    an echo freshened."""
    reg = NodeRegistry()
    reg.ingest(HASH, beacon(), NOW)
    reg.ingest(HASH, beacon(), NOW + 0.9 * HOUR)      # byte-identical = replay
    d = reg.get(HASH).to_dashboard(NOW + HOUR)
    assert d["last_echo_hours"] == pytest.approx(0.1)
    assert d["last_echo_hours"] < d["last_seen_hours"]  # echo is the fresher
    assert d["last_seen_hours"] == pytest.approx(1.0)   # sighting NOT refreshed
    assert d["last_direct_hours"] == pytest.approx(1.0)  # the tag's gate rides too
    assert d["status"] == "ok"                # 1h-old beacon; the echo added
    # ...nothing. And once stale, an even-fresh echo must not soften the red:
    late = NOW + (STALE_ALERT_HOURS + 2) * HOUR
    reg.ingest(HASH, beacon(), late - 60)               # replay a minute ago
    assert reg.get(HASH).to_dashboard(late)["status"] == "alert"


def test_to_dashboard_no_echo_is_none():
    reg = NodeRegistry()
    reg.ingest(HASH, beacon(), NOW)
    assert reg.get(HASH).to_dashboard(NOW)["last_echo_hours"] is None


def test_devices_row_pools_the_echo():
    """The consolidated dashboard row carries the echo age too (freshest of a
    device's aspects), so VITALS — which renders devices(), not raw records —
    can actually show it. The direct age pools with it: the tag is gated on
    the device's freshest DIRECT word, and one aspect's replay must never be
    silenced by nothing — nor pass for another aspect's live word."""
    reg = NodeRegistry()
    reg.ingest(HASH, beacon(), NOW)
    reg.ingest(HASH, beacon(), NOW + 0.5 * HOUR)
    row = [d for d in reg.devices(NOW + HOUR) if d["identity"] == HASH][0]
    assert row["last_echo_hours"] == pytest.approx(0.5)
    assert row["last_seen_hours"] == pytest.approx(1.0)
    assert row["last_direct_hours"] == pytest.approx(1.0)


def test_clock_skew_cannot_turn_an_echo_negative():
    """A clock stepping backwards past the echo must not hand the display a
    negative age — a sentinel there once meant "no echo", and node detail
    would have rendered the nonsense straight. Clamped in the record methods
    so every surface (to_dashboard, devices, node detail) agrees by
    construction."""
    reg = NodeRegistry()
    reg.ingest(HASH, beacon(), NOW)
    reg.ingest(HASH, beacon(), NOW + 100)               # replay
    d = reg.get(HASH).to_dashboard(NOW + 50)            # clock stepped back
    assert d["last_echo_hours"] == 0.0                  # clamped, still real
    assert reg.get(HASH).last_echo_hours(NOW + 50) == 0.0
    assert reg.get(HASH).last_direct_hours(NOW - 50) == 0.0


# ---- SEEN honesty: never-heard + clock-step (dead-board-green class) -----
# set_kin_roster seeds fleet rows before first contact; last_seen_hours used
# to collapse that None to 0.0 and paint "SEEN 0.0h" GREEN — the exact
# dead-board-green failure (2026-08-21), reached again through a backward
# clock step that made the age negative.


def test_never_heard_node_is_not_green_and_renders_never():
    from monitor.formatting import seen_and_echo, seen_is_known
    reg = NodeRegistry()
    reg.register(HASH, name="FLEETNODE")      # seeded, never heard
    d = reg.get(HASH).to_dashboard(NOW)
    assert d["has_seen"] is False
    assert d["seen_impossible"] is False
    assert d["status"] == "unknown"           # hexagon is not healthy
    assert reg.get(HASH).last_seen_hours(NOW) is None
    # render helpers: "SEEN never", grey (not green)
    assert seen_and_echo(d) == ("SEEN never", None)
    assert seen_is_known(d) is False


def test_clock_step_reading_is_not_green_and_renders_question():
    from monitor.formatting import seen_and_echo, seen_is_known
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(), NOW)          # heard at NOW
    earlier = NOW - 50 * HOUR                           # clock stepped back
    rec = reg.get(HASH)
    # the age accessor clamps negative -> 0.0, like its echo/direct siblings
    assert rec.last_seen_hours(earlier) == 0.0
    d = rec.to_dashboard(earlier)
    assert d["seen_impossible"] is True
    assert d["has_seen"] is True
    assert d["last_seen_hours"] == 0.0
    assert d["status"] == "unknown"           # never green off a negative age
    # render helpers: "SEEN ?", grey (not the clamped "0.0h" green)
    assert seen_and_echo(d) == ("SEEN ?", None)
    assert seen_is_known(d) is False


def test_tiny_backward_skew_stays_green_not_grey():
    """A benign sub-two-minute backward clock jitter must NOT flip a node heard
    seconds ago to grey "SEEN ?" — that is a dishonesty the other way (crying
    unknown on a live node). Inside the deadband the age clamps to fresh."""
    from monitor.formatting import seen_and_echo, seen_is_known
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(status="ok"), NOW)   # heard at NOW
    skewed = NOW - 3                                        # clock jittered 3s
    rec = reg.get(HASH)
    assert rec.last_seen_hours(skewed) == 0.0
    d = rec.to_dashboard(skewed)
    assert d["seen_impossible"] is False       # jitter, not a genuine step
    assert d["has_seen"] is True
    assert d["status"] == "ok"                  # still green
    assert seen_and_echo(d) == ("SEEN 0.0h", None)
    assert seen_is_known(d) is True


def test_fresh_node_still_dashboards_green_and_normal():
    from monitor.formatting import seen_and_echo, seen_is_known
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(status="ok"), NOW)
    d = reg.get(HASH).to_dashboard(NOW + HOUR)
    assert d["has_seen"] is True
    assert d["seen_impossible"] is False
    assert d["status"] == "ok"
    assert d["last_seen_hours"] == pytest.approx(1.0)
    assert seen_and_echo(d) == ("SEEN 1.0h", None)
    assert seen_is_known(d) is True


# ---- HTTP /status must not set an arbitrary node name --------------------
# The ANNOUNCE path validates names (_valid_display_name); the HTTP adoption
# path did not, so a hostile /status could name a node anything.


def test_http_rejects_overlong_node_name():
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(name="x" * 40), NOW)   # > 32 chars
    assert reg.get(HASH).name == ""            # refused, not adopted


def test_http_rejects_non_string_node_name_without_raising():
    reg = NodeRegistry()
    # a non-string must be refused, never raise on .strip()/.lower()
    reg.record_http_status(HASH, http(name=12345), NOW)
    assert reg.get(HASH).name == ""


def test_http_rejects_unprintable_node_name():
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(name="ok\x07node"), NOW)
    assert reg.get(HASH).name == ""


def test_http_adopts_a_valid_node_name():
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(name="GOODNAME"), NOW)
    assert reg.get(HASH).name == "GOODNAME"


# ---- mesh ingest (rnpath reachability) ----------------------------------


def _mesh(dst, hops=1, iface="RNodeInterface[RNode LoRa Interface]", heard=None):
    """A path table row. *heard* is when the path was LEARNED — which is when we
    last actually heard the node; defaults to NOW for the fresh-sighting case."""
    from monitor.mesh import MeshNode
    return MeshNode(dst_hash=dst, hops=hops, interface=iface,
                    heard=NOW if heard is None else heard)


def test_ingest_mesh_registers_and_marks_reachable():
    reg = NodeRegistry()
    rec = reg.ingest_mesh(_mesh(HASH, hops=2), NOW)
    assert rec.dst_hash == HASH
    assert rec.mesh_hops == 2
    assert rec.last_seen == NOW
    # reachable via mesh, health unknown -> ok (not "unknown")
    assert rec.status(NOW) == "ok"


def test_mesh_only_node_goes_alert_when_stale():
    reg = NodeRegistry()
    reg.ingest_mesh(_mesh(HASH), NOW)
    assert reg.get(HASH).status(NOW + (STALE_ALERT_HOURS + 1) * HOUR) == "alert"


def test_a_path_still_in_the_table_is_not_a_sighting():
    """SolarLove, 2026-08-11: unplugged for most of a day and still showing
    green, SEEN 0.0h, in VITALS. Reticulum keeps a learned path for SEVEN DAYS
    after the announce that taught it, and every scan was stamping last_seen =
    now for every row in the table — so a dead node reads as live for a week on
    the one screen an operator uses to find out whether it is alive."""
    reg = NodeRegistry()
    learned = NOW - 19 * HOUR                     # the real last announce
    reg.ingest_mesh(_mesh(HASH, heard=learned), NOW)
    rec = reg.get(HASH)
    assert rec.last_seen == learned
    assert round(rec.last_seen_hours(NOW)) == 19
    assert rec.status(NOW) == "alert"             # and it says so


def test_a_route_never_overwrites_having_actually_heard_the_node():
    """A health beacon or an HTTP poll is the node speaking; a path row is only
    a route to it. Folding in a stale route must not undo the real thing."""
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(status="ok"), NOW)
    reg.ingest_mesh(_mesh(HASH, heard=NOW - 40 * HOUR), NOW)
    rec = reg.get(HASH)
    assert rec.last_seen == NOW              # the direct evidence stands
    assert rec.mesh_heard == NOW - 40 * HOUR  # and the route is recorded as old


def test_a_record_poisoned_by_the_old_behaviour_repairs_itself():
    """Every record already on the medic holds a last_seen of "whenever the last
    scan ran", because that is what the old code wrote. A max() against the
    stored value would defend that wrong number forever, so the mesh sighting is
    RECOMPUTED from the path row each time."""
    reg = NodeRegistry()
    poisoned = reg.register(HASH)
    poisoned.last_seen = NOW                  # what the old scan left behind
    reg.ingest_mesh(_mesh(HASH, heard=NOW - 19 * HOUR), NOW)
    assert reg.get(HASH).last_seen == NOW - 19 * HOUR


def test_a_path_row_with_no_timestamp_invents_nothing():
    """An older rnpath gives no timestamp. Not knowing when we heard a node is
    not the same as having heard it just now."""
    reg = NodeRegistry()
    rec = reg.ingest_mesh(_mesh(HASH, heard=0.0), NOW)
    assert rec.last_seen is None
    assert rec.mesh_hops == 1                     # reachability still recorded


def test_http_health_still_preferred_over_mesh_reachability():
    reg = NodeRegistry()
    reg.ingest_mesh(_mesh(HASH), NOW)
    reg.record_http_status(HASH, http(status="warn"), NOW)   # richer signal wins
    assert reg.get(HASH).status(NOW) == "warn"


def test_ingest_mesh_keeps_known_node_name():
    reg = NodeRegistry()
    reg.register(HASH, name="EVERYWHERE", location="House")
    reg.ingest_mesh(_mesh(HASH), NOW)
    assert reg.get(HASH).name == "EVERYWHERE"    # birth-cert name preserved

HASH = "11223344556677889900aabbccddeeff"
HASH2 = "aa11bb22cc33dd44ee55ff6600778899"

HOUR = 3600.0
NOW = 1_000_000.0


def beacon(**over):
    kw = dict(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
              wifi_up=True, lora_up=True, tcp_backbone_up=True,
              local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
              board_id=0x3F, fw=(0, 6, 2))
    kw.update(over)
    return decode(encode(**kw))


def line(dst=HASH, **over):
    kw = dict(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
              wifi_up=True, lora_up=True, tcp_backbone_up=True,
              local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
              board_id=0x3F, fw=(0, 6, 2))
    kw.update(over)
    return f"[HealthBeacon] announce dst={dst} data={encode(**kw).hex()}"


def test_register_creates_node_with_metadata():
    r = NodeRegistry()
    rec = r.register(HASH, name="TRUTH", location="Wrenhill", node_type="rtnode2400")
    assert isinstance(rec, NodeRecord)
    assert r.get(HASH).name == "TRUTH"
    assert r.get(HASH).location == "Wrenhill"


def test_ingest_updates_beacon_and_last_seen():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    r.ingest(HASH, beacon(), NOW)
    rec = r.get(HASH)
    assert rec.latest_beacon is not None
    assert rec.last_seen == NOW
    assert rec.status(NOW) == "ok"


def test_ingest_unknown_hash_auto_registers():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    assert r.get(HASH) is not None      # first-seen node appears
    assert r.get(HASH).status(NOW) == "ok"


def test_fault_beacon_is_alert():
    r = NodeRegistry()
    r.ingest(HASH, beacon(fault=True), NOW)
    assert r.get(HASH).status(NOW) == "alert"


def test_weak_wifi_is_warn():
    r = NodeRegistry()
    r.ingest(HASH, beacon(wifi_rssi_dbm=-80), NOW)
    assert r.get(HASH).status(NOW) == "warn"


def test_staleness_over_six_hours_is_alert_even_if_last_ok():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)               # last beacon was OK
    later = NOW + (STALE_ALERT_HOURS + 0.5) * HOUR
    assert r.get(HASH).status(later) == "alert"  # not heard -> red


def test_recent_ok_within_window_stays_ok():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    assert r.get(HASH).status(NOW + 2 * HOUR) == "ok"


def test_never_heard_is_unknown():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    assert r.get(HASH).status(NOW) == "unknown"


def test_last_seen_hours():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    assert r.get(HASH).last_seen_hours(NOW + 3 * HOUR) == pytest.approx(3.0)


def test_ingest_line_parses_and_stores():
    r = NodeRegistry()
    rec = r.ingest_line(line(), NOW)
    assert rec is not None
    assert r.get(HASH).status(NOW) == "ok"
    assert r.get(HASH).latest_beacon.firmware_version == "0.6.2"


def test_ingest_line_ignores_non_beacon():
    r = NodeRegistry()
    assert r.ingest_line("[WATCHDOG] heap=180000", NOW) is None
    assert r.ingest_line("garbage", NOW) is None


def test_summary_counts_by_status():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)                       # ok
    r.ingest(HASH2, beacon(fault=True), NOW)            # alert
    s = r.summary(NOW)
    assert s["ok"] == 1
    assert s["alert"] == 1
    assert s["warn"] == 0


def test_filter_by_status_and_search():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    r.ingest(HASH, beacon(), NOW)
    r.register(HASH2, name="Ironbark")
    r.ingest(HASH2, beacon(fault=True), NOW)
    ok_nodes = r.visible(NOW, status="ok")
    assert [n.name for n in ok_nodes] == ["TRUTH"]
    found = r.visible(NOW, search="iron")
    assert [n.name for n in found] == ["Ironbark"]


def test_all_sorted_alert_first():
    r = NodeRegistry()
    r.register(HASH, name="Aaa"); r.ingest(HASH, beacon(), NOW)               # ok
    r.register(HASH2, name="Bbb"); r.ingest(HASH2, beacon(fault=True), NOW)   # alert
    names = [n.name for n in r.all(NOW)]
    assert names[0] == "Bbb"    # alert first


def test_record_poll_ingests_clean_reply():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    result = PollResult(node_status="ok", reachable=True, attempts=1, beacon=beacon())
    r.record_poll(HASH, result, NOW)
    assert r.get(HASH).status(NOW) == "ok"   # cleared to green


def test_record_poll_unreachable_does_not_update_last_seen():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH")
    result = PollResult(node_status="unreachable", reachable=False, attempts=3, beacon=None)
    r.record_poll(HASH, result, NOW)
    assert r.get(HASH).last_seen is None


# --- an unanswered probe outranks a stale green (seed, 2026-08-20) -----------

def test_unanswered_probe_demotes_a_green_face_to_warn():
    """seed was powered OFF; the operator's ping said unreachable; the tile
    stayed green off a 40-minute-old beacon. The newest direct evidence — the
    tool's own failed interrogation — must win the face."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)                    # clean beacon → green
    assert r.get(HASH).status(NOW + 2400) == "ok"
    r.record_probe(HASH, ok=False, now=NOW + 2400)   # ping went unanswered
    assert r.get(HASH).status(NOW + 2400) == "warn"


def test_a_newer_beacon_clears_the_unanswered_probe():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.record_probe(HASH, ok=False, now=NOW + 100)
    assert r.get(HASH).status(NOW + 100) == "warn"
    # uptime has TICKED — a live node's next beacon is never byte-identical
    # to its last (the replay guard would rightly ignore an exact copy)
    r.ingest(HASH, beacon(uptime_s=236), NOW + 200)  # the node itself speaks
    assert r.get(HASH).status(NOW + 200) == "ok"
    assert r.get(HASH).poll_failed_at is None


def test_an_answered_probe_clears_the_failure_too():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.record_probe(HASH, ok=False, now=NOW + 100)
    r.record_probe(HASH, ok=True, now=NOW + 200)     # rnpath proved it live
    assert r.get(HASH).status(NOW + 200) == "ok"


def test_a_replayed_beacon_is_not_a_sighting():
    """2026-08-21: a battery-less board's row was "seen" 90 s after unplugging
    — rnsd replayed its cached announce on a path request. Identical bytes are
    the transport echoing, not the node speaking: no last_seen refresh, no
    history point, and above all no curing of a failed poll."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.record_probe(HASH, ok=False, now=NOW + 100)
    n_hist = len(r.history.series(HASH))
    r.ingest(HASH, beacon(), NOW + 200)              # byte-identical = replay
    rec = r.get(HASH)
    assert rec.last_seen == NOW                       # unchanged
    assert rec.last_echo_at == NOW + 200              # recorded for diagnosis
    assert rec.poll_failed_at == NOW + 100            # failure NOT cured
    assert rec.status(NOW + 200) == "warn"
    assert len(r.history.series(HASH)) == n_hist      # no fake activity


def test_a_changed_beacon_still_counts():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.ingest(HASH, beacon(uptime_s=96), NOW + 60)
    assert r.get(HASH).last_seen == NOW + 60


def test_last_echo_at_survives_save_and_load():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.ingest(HASH, beacon(), NOW + 200)
    r2 = NodeRegistry.from_dict(r.to_dict())
    assert r2.get(HASH).last_echo_at == NOW + 200


# ---- bare-announce replays (the non-beacon twin of the guard above) ------


def test_a_replayed_bare_announce_is_an_echo_too():
    """rnsd replays BARE announces from its cache byte-for-byte as well, and
    the non-beacon branch used to launder that copy into a genuine sighting
    (last_seen AND last_direct) — which, pooled across a multi-aspect device
    (the Pi propagation-node case), buried the echo tag the 2026-08-21 dead
    board earned. Identical payload bytes already heard = the transport
    speaking, not the node."""
    r = NodeRegistry()
    raw = bytes.fromhex(HASH)
    r.ingest_announce(raw, b"WILDNODE", NOW)
    n_hist = len(r.history.series(HASH))
    r.ingest_announce(raw, b"WILDNODE", NOW + 200)   # byte-identical = replay
    rec = r.get(HASH)
    assert rec.last_seen == NOW                       # unchanged
    assert rec.last_direct == NOW                     # unchanged
    assert rec.last_echo_at == NOW + 200              # recorded for diagnosis
    assert len(r.history.series(HASH)) == n_hist      # no fake activity


def test_a_changed_bare_announce_is_a_sighting():
    r = NodeRegistry()
    raw = bytes.fromhex(HASH)
    r.ingest_announce(raw, b"WILDNODE", NOW)
    r.ingest_announce(raw, b"WILDNODE-2", NOW + 200)  # new bytes = node spoke
    rec = r.get(HASH)
    assert rec.last_seen == NOW + 200
    assert rec.last_direct == NOW + 200
    assert rec.last_echo_at is None


def test_payloadless_announces_are_exempt_from_replay_detection():
    """No payload, nothing to compare — "identical" cannot be established,
    and never guess. Every empty announce stays a sighting."""
    r = NodeRegistry()
    raw = bytes.fromhex(HASH)
    r.ingest_announce(raw, None, NOW)
    r.ingest_announce(raw, None, NOW + 200)
    rec = r.get(HASH)
    assert rec.last_seen == NOW + 200
    assert rec.last_echo_at is None


def test_announce_fingerprint_survives_save_and_load():
    """A replay across a restart is still a replay — the fingerprint rides
    the registry file like last_echo_at does."""
    r = NodeRegistry()
    r.ingest_announce(bytes.fromhex(HASH), b"WILDNODE", NOW)
    r2 = NodeRegistry.from_dict(r.to_dict())
    r2.ingest_announce(bytes.fromhex(HASH), b"WILDNODE", NOW + 200)
    assert r2.get(HASH).last_seen == NOW
    assert r2.get(HASH).last_echo_at == NOW + 200


def test_unanswered_probe_never_upgrades_a_worse_face():
    """Demote-only: an alert node stays alert; the probe failure must not
    LAUNDER a red face into amber."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(fault=True), NOW)          # alert
    r.record_probe(HASH, ok=False, now=NOW + 100)
    assert r.get(HASH).status(NOW + 100) == "alert"


def test_record_poll_failure_now_stamps_the_record():
    """record_poll used to DISCARD silence ("staleness will take it red on
    its own") — hours of green lie. It stamps now."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    result = PollResult(node_status="unreachable", reachable=False,
                        attempts=3, beacon=None)
    r.record_poll(HASH, result, NOW + 100)
    assert r.get(HASH).probe_unanswered
    assert r.get(HASH).status(NOW + 100) == "warn"


def test_poll_failed_at_survives_a_restart():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.record_probe(HASH, ok=False, now=NOW + 100)
    r2 = NodeRegistry.from_dict(r.to_dict())
    assert r2.get(HASH).poll_failed_at == NOW + 100
    assert r2.get(HASH).status(NOW + 100) == "warn"


def test_ingest_announce_adapter_decodes_and_stores():
    r = NodeRegistry()
    dst = bytes.fromhex(HASH)
    app_data = encode(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
                      wifi_up=True, lora_up=True, tcp_backbone_up=True,
                      local_tcp_server_up=True, wdt_armed=True, psram=True,
                      fault=False, board_id=0x3F, fw=(0, 6, 2))
    rec = r.ingest_announce(dst, app_data, NOW)
    assert rec is not None
    assert r.get(HASH).status(NOW) == "ok"
    assert r.get(HASH).latest_beacon.board_label == "Heltec32 V4"


def test_located_nodes_only_returns_geotagged():
    r = NodeRegistry()
    r.register(HASH, name="FAITH")
    rec = r.get(HASH); rec.lat = -37.814; rec.lon = 144.963
    r.ingest(HASH, beacon(), NOW)                 # gives it an 'ok' status
    r.register(HASH2, name="NOLOC")               # no coordinates -> omitted
    pts = r.located_nodes(NOW)
    assert [p["name"] for p in pts] == ["FAITH"]
    assert pts[0]["lat"] == -37.814 and pts[0]["lon"] == 144.963
    assert pts[0]["status"] == "ok"


def test_located_nodes_empty_when_none_geotagged():
    r = NodeRegistry()
    r.register(HASH, name="NOLOC")
    assert r.located_nodes(NOW) == []


def test_ingest_announce_without_beacon_still_marks_heard():
    # changed 2026-07-19: a non-beacon announce no longer vanishes — the node
    # is registered as a heard neighbour (honest last-seen), just without
    # health data.
    r = NodeRegistry()
    rec = r.ingest_announce(bytes.fromhex(HASH), b"\x01\x02", NOW)
    assert rec is not None and rec.last_seen == NOW
    assert rec.latest_beacon is None


# ---- device consolidation + capabilities (#54) -----------------------------

def test_same_identity_destinations_collapse_to_one_device():
    reg = NodeRegistry()
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"\x06Pebble", 1000.0,
                        identity_hash="ident1")
    reg.ingest_announce(bytes.fromhex("bb" * 16), b"", 2000.0,
                        identity_hash="ident1")
    rows = reg.devices(now=2000.0)
    assert len(rows) == 1                          # one phone, not two rows
    assert rows[0]["aspects"] == 2
    assert rows[0]["name"] == "Pebble"             # announced name surfaces
    assert rows[0]["last_seen_hours"] == 0.0       # freshest member wins


def test_devices_marks_quiet_after_threshold_and_a_ping_lifts_it():
    from monitor.registry import QUIET_AFTER_HOURS
    reg = NodeRegistry()
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"\x06Pebble", 1000.0,
                        identity_hash="i1")
    assert reg.devices(now=1000.0)[0]["quiet"] is False        # just heard
    later = 1000.0 + (QUIET_AFTER_HOURS + 1) * 3600
    assert reg.devices(now=later)[0]["quiet"] is True          # gone quiet
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"", later, identity_hash="i1")
    assert reg.devices(now=later)[0]["quiet"] is False         # a ping lifts it back


def test_save_load_round_trip_preserves_history(tmp_path):
    reg = NodeRegistry()
    key = "cd" * 16
    reg.register(key, name="Rooftop", lat=-37.8, lon=145.0)
    reg.ingest_announce(bytes.fromhex(key), b"", 1000.0, identity_hash="i9")
    reg.ingest_announce(bytes.fromhex(key), b"", 8200.0, identity_hash="i9")
    path = str(tmp_path / "sub" / "registry.json")     # nested dir auto-created
    assert reg.save(path) is True
    back = NodeRegistry.load(path)
    assert back.get(key).name == "Rooftop"
    assert [p.t for p in back.history.series(key)] == [1000.0, 8200.0]


def test_load_missing_or_corrupt_starts_clean(tmp_path):
    assert NodeRegistry.load(str(tmp_path / "nope.json")).nodes == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert NodeRegistry.load(str(bad)).nodes == {}


def test_plain_announce_accumulates_activity_history():
    # a bare (non-beacon) announce should still leave a heard-event point, so
    # intermittent / neighbour nodes build a "when are they up?" series.
    reg = NodeRegistry()
    key = "aa" * 16
    reg.ingest_announce(bytes.fromhex(key), b"", 1000.0, identity_hash="i1")
    reg.ingest_announce(bytes.fromhex(key), b"", 8200.0, identity_hash="i1")
    pts = reg.history.series(key)
    assert [p.t for p in pts] == [1000.0, 8200.0]


def test_different_identities_stay_separate():
    reg = NodeRegistry()
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"", 1000.0, identity_hash="i1")
    reg.ingest_announce(bytes.fromhex("bb" * 16), b"", 1000.0, identity_hash="i2")
    assert len(reg.devices(now=1000.0)) == 2


def test_capabilities_lora_true_when_heard_on_the_radio():
    reg = NodeRegistry()
    class _Mesh:
        dst_hash = "cc" * 16
        hops = 1
        interface = "RNodeInterface[RNode LoRa Interface]"
    reg.ingest_mesh(_Mesh(), now=1000.0)
    caps = reg.devices(now=1000.0)[0]["capabilities"]
    assert caps["lora"] is True
    assert caps["bluetooth"] is None               # unknowable -> grey
    assert caps["wifi"] is None


def test_capabilities_from_a_health_beacon():
    reg = NodeRegistry()
    reg.ingest(HASH, decode(encode(uptime_s=5, heap_kb=100, wifi_rssi_dbm=-60,
                                   reset_reason=0, wifi_up=True, lora_up=True,
                                   tcp_backbone_up=False,
                                   local_tcp_server_up=True, wdt_armed=True,
                                   psram=True, fault=False, board_id=0x3F,
                                   fw=(0, 6, 2))), now=1000.0)
    caps = reg.devices(now=1000.0)[0]["capabilities"]
    assert caps["wifi"] is True
    assert caps["internet"] is False               # node says backbone down


def test_kin_devices_sort_above_neighbours():
    reg = NodeRegistry()
    reg.ingest_announce(bytes.fromhex("aa" * 16), b"", 1000.0, identity_hash="n1")
    reg.register("dd" * 16, name="MyNode")         # named = kin
    rows = reg.devices(now=1000.0)
    assert rows[0]["name"] == "MyNode"
    assert rows[1]["provenance"] == "neighbour"


def test_announce_marks_neighbour_heard_now_not_path_table_stale():
    reg = NodeRegistry()
    rec = reg.ingest_announce(bytes.fromhex("ee" * 16), b"", 5000.0)
    assert rec.last_seen == 5000.0                 # honest last-heard
    assert rec.provenance == "neighbour"           # announced name != kin


def test_devices_collapses_same_name_across_destinations():
    """FAITH reached 3 ways (health-beacon dst, HTTP /status keyed by name, an
    rnpath path) must render as ONE device row, not three."""
    from monitor.registry import NodeRegistry
    reg = NodeRegistry()
    # kin/health record under the beacon dest, named via the kin roster
    reg.set_kin_roster({"b7c8d9e0": {"name": "FAITH RTnode", "type": "rtnode2400",
                                     "builder": "medic-unit"}})
    reg.ingest("b7c8d9e0", beacon(), NOW)
    # HTTP /status record keyed by name (no shared identity_hash)
    reg.record_http_status("rtnode:FAITH RTnode", http(name="FAITH RTnode"), NOW)
    # a bare rnpath neighbour with a DIFFERENT device, unnamed -> stays separate
    from monitor.mesh import MeshNode
    reg.ingest_mesh(MeshNode(dst_hash="deadbeef", hops=2, interface="LoRa"), NOW)

    rows = reg.devices(NOW)
    faith = [d for d in rows if d["name"] == "FAITH RTnode"]
    assert len(faith) == 1, f"FAITH should collapse to one row, got {len(faith)}"
    # the merged row keeps kin provenance and unions capabilities
    assert faith[0]["provenance"] == "kin"
    assert faith[0]["aspects"] >= 2
    # the unrelated unnamed neighbour is still its own row
    assert any(d["provenance"] == "neighbour" for d in rows)


# -- probe_hash_for: resolve a probeable mesh dest for a device --------------

def test_probe_hash_for_returns_hex_key_directly():
    reg = NodeRegistry()
    h = "b7c8d9e0f1a2b3c4d5e6f70819a2b3c4"
    reg.register(h, name="FAITH RTnode")
    assert reg.probe_hash_for(h) == h


def test_probe_hash_for_resolves_non_hex_key_by_name():
    # A non-hex HTTP-discovery row 'rtnode:FAITH RTnode' shares a NAME with the
    # hex mesh dest — probe must resolve to the hex one.
    reg = NodeRegistry()
    hexh = "b7c8d9e0f1a2b3c4d5e6f70819a2b3c4"
    reg.register(hexh, name="FAITH RTnode")
    reg.register("rtnode:FAITH RTnode", name="FAITH RTnode")   # HTTP key, non-hex
    assert reg.probe_hash_for("rtnode:FAITH RTnode") == hexh


def test_probe_hash_for_resolves_by_identity():
    reg = NodeRegistry()
    hexh = "aabbccddeeff00112233445566778899"
    reg.register(hexh)
    reg.nodes[hexh].identity_hash = "778899aabbccddeeff0011223344556f"
    reg.register("rtnode:FAITH RTnode", name="FAITH RTnode")
    reg.nodes["rtnode:FAITH RTnode"].identity_hash = "778899aabbccddeeff0011223344556f"
    assert reg.probe_hash_for("rtnode:FAITH RTnode") == hexh


def test_probe_hash_for_none_when_no_hex_dest():
    reg = NodeRegistry()
    reg.register("rtnode:Lonely", name="Lonely")   # only a non-hex key, no siblings
    assert reg.probe_hash_for("rtnode:Lonely") is None
    assert reg.probe_hash_for("no-such-key") is None


# -- consolidated_record: device-level health for the node-detail screen ------

def _faith_registry():
    """The FAITH case: a hex health-beacon dest carrying the decoded beacon, plus
    a non-hex HTTP `rtnode:<name>` aspect sharing the name but with NO beacon."""
    hexh = "b7c8d9e0f1a2b3c4d5e6f70819a2b3c4"
    reg = NodeRegistry()
    reg.set_kin_roster({hexh: {"name": "FAITH RTnode", "type": "rtnode2400",
                               "builder": "medic-unit"}})
    reg.ingest(hexh, beacon(uptime_s=999), NOW)                 # beacon lives here
    reg.record_http_status("rtnode:FAITH RTnode", http(name="FAITH RTnode"), NOW)
    return reg, hexh


def test_consolidated_record_carries_beacon_from_health_bearing_member():
    """Tapping the row via its beacon-less HTTP aspect must still surface the
    device's decoded beacon (the node-detail Health text), not 'none yet'."""
    from monitor.formatting import beacon_lines
    reg, hexh = _faith_registry()
    # the beacon-less HTTP record on its own reads empty
    assert beacon_lines(reg.get("rtnode:FAITH RTnode")) == \
        ["No health beacon received yet."]
    # consolidating from EITHER key yields the device's real beacon
    for key in ("rtnode:FAITH RTnode", hexh):
        view = reg.consolidated_record(key, NOW)
        assert view is not None
        assert view.latest_beacon is not None
        assert view.latest_beacon.uptime_s == 999
        assert any("Uptime: 999s" in ln for ln in beacon_lines(view))


def test_consolidated_record_status_matches_dashboard_dot():
    """The node-detail hexagon (view.status) must equal the VITALS dot (the
    dashboard row status) for the same device."""
    reg, hexh = _faith_registry()
    row = [d for d in reg.devices(NOW) if d["name"] == "FAITH RTnode"][0]
    for key in ("rtnode:FAITH RTnode", hexh):
        view = reg.consolidated_record(key, NOW)
        assert view.status(NOW) == row["status"]


def test_consolidated_record_keeps_name_and_kin_provenance():
    """Health is pooled, but the merged record keeps its authoritative kin
    identity — the detail header stays 'FAITH RTnode', not a bare hash."""
    reg, hexh = _faith_registry()
    view = reg.consolidated_record("rtnode:FAITH RTnode", NOW)
    assert view.name == "FAITH RTnode"
    assert view.provenance == "kin"


def test_consolidated_record_does_not_mutate_stored_records():
    """The consolidated view is a copy: pooling health onto it must not leak the
    beacon onto the stored beacon-less HTTP record."""
    reg, hexh = _faith_registry()
    reg.consolidated_record("rtnode:FAITH RTnode", NOW)
    assert reg.get("rtnode:FAITH RTnode").latest_beacon is None


def test_consolidated_record_none_for_unknown_key():
    reg, _ = _faith_registry()
    assert reg.consolidated_record("no-such-key", NOW) is None


def test_the_two_kinds_of_evidence_survive_a_restart():
    """Without persisting them the app comes back unable to tell a route from
    having heard the node, and the first mesh scan overwrites fresh direct
    evidence with an old path timestamp."""
    reg = NodeRegistry()
    reg.record_http_status(HASH, http(status="ok"), NOW)
    reg.ingest_mesh(_mesh(HASH, heard=NOW - 40 * HOUR), NOW)
    back = NodeRegistry.from_dict(reg.to_dict())
    rec = back.get(HASH)
    assert rec.last_direct == NOW and rec.mesh_heard == NOW - 40 * HOUR
    back.ingest_mesh(_mesh(HASH, heard=NOW - 40 * HOUR), NOW)
    assert back.get(HASH).last_seen == NOW


# --- forget a node completely, so its name can be reborn --------------------

def test_forget_node_removes_every_row_of_the_machine():
    """One machine leaves several rows (the build's placeholder, plus one per
    announced destination, grouped by identity). Deleting a node from its
    detail page must take ALL of them and their history, or the reborn name
    inherits a stranger's past (operator request, 2026-08-13)."""
    r = NodeRegistry()
    r.register("rtnode:ttt", name="ttt", node_type="pi_propagation")
    r.ingest_announce(bytes.fromhex("72" * 16), b"", 1000.0,
                      identity_hash="aa" * 16)
    r.ingest_announce(bytes.fromhex("96" * 16), b"", 1001.0,
                      identity_hash="aa" * 16)
    r.nodes[bytes.fromhex("72" * 16).hex()].name = "TTT"
    removed = r.forget_node("ttt")
    assert removed == 3
    assert not any((v.name or "").lower() == "ttt" for v in r.nodes.values())
    assert bytes.fromhex("96" * 16).hex() not in r.nodes   # identity sibling went too
    assert bytes.fromhex("72" * 16).hex() not in r.history._series if hasattr(r.history, "_series") else True


def test_forget_node_leaves_strangers_alone():
    r = NodeRegistry()
    r.register("rtnode:ttt", name="ttt")
    r.register("rtnode:hope", name="HOPE")
    r.forget_node("ttt")
    assert any((v.name or "") == "HOPE" for v in r.nodes.values())


def test_forget_node_unknown_name_is_zero_not_error():
    r = NodeRegistry()
    assert r.forget_node("nonesuch") == 0


def test_the_detail_screen_offers_delete_behind_the_danger_confirm():
    """Operator request, 2026-08-13: a delete button on the node detail page
    that removes ALL data about the node so the name can be reused. It must
    sit behind confirm_danger (destructive), say it deletes the MEDIC'S
    record not the node, and the app hook must clear registry rows, certs,
    roster and beacon targets."""
    from tests.srcutil import func_source, src as read_src
    detail = read_src("ui/screens/node_detail_screen.py")
    assert "Delete this node" in detail
    assert "confirm_danger" in detail
    forget = func_source("ui/app.py", "_forget_node")
    assert "forget_node" in forget          # registry, all sibling rows
    assert "delete_by_name" in forget       # certificates
    assert "kin_roster" in forget           # roster entry
    assert "_beacon_targets" in forget      # poll targets
    assert 'switch_mode("vitals")' in forget


# --- one machine, one row — the two-agent findings, merged ------------------

def _roster(monkeypatch, entries):
    r = NodeRegistry()
    r.kin_roster = entries
    return r

def test_aspect_row_adopts_the_roster_through_its_identity():
    """build-lens, 2026-08-13: _apply_kin consulted the roster only by
    dst_hash, and identity_hash lands AFTER register() — so a destination the
    roster never listed stayed an anonymous sibling (S3: two rows). The
    roster must be re-applied once the identity is known."""
    r = NodeRegistry()
    r.kin_roster = {"idhash00": {"name": "ttt", "type": "pi_propagation",
                                 "device": "healthdst0"}}
    r.ingest_announce(b"\x96" * 16, b"", 1000.0, identity_hash="idhash00")
    rec = r.nodes[("96" * 16)]
    assert rec.name == "ttt"
    assert rec.device_id == "healthdst0"


def test_discovery_placeholder_joins_its_machine_by_roster_name():
    """The rtnode:<name> discovery row was joined by nothing but a case-folded
    display name. When /status names a rostered node, the row takes the
    roster's device id — a real join, not a coincidence of spelling."""
    from monitor.http_status import NodeStatus
    r = NodeRegistry()
    r.kin_roster = {"healthdst0": {"name": "ttt", "type": "pi_propagation",
                                   "device": "healthdst0"}}
    st = NodeStatus(reachable=True, status="ok", node_name="ttt", raw={})
    r.record_http_status("rtnode:ttt", st, 1000.0)
    assert r.nodes["rtnode:ttt"].device_id == "healthdst0"


def test_two_machines_sharing_a_name_stay_two_rows():
    """break-lens, 2026-08-13: the unconditional name-collapse folded a dead
    machine under a same-named live one — max(last_seen) made the corpse
    invisible. Two rows with DIFFERENT known identities never merge on
    spelling; the dead one keeps its own red."""
    r = NodeRegistry()
    r.ingest_announce(b"\xaa" * 16, b"", 1000.0, identity_hash="ident-dead")
    r.ingest_announce(b"\xbb" * 16, b"", 200000.0, identity_hash="ident-live")
    r.nodes["aa" * 16].name = "Relay"
    r.nodes["bb" * 16].name = "Relay"
    groups = r._device_groups()
    assert len(groups) == 2, "a corpse hid behind a live namesake"


def test_a_nameless_placeholder_still_joins_its_named_machine():
    """The guard must not undo the good merge: a discovery row (no identity)
    sharing the machine's name still folds in."""
    r = NodeRegistry()
    r.ingest_announce(b"\xcc" * 16, b"", 1000.0, identity_hash="ident-one")
    r.nodes["cc" * 16].name = "HOPE"
    r.register("rtnode:hope", name="HOPE")
    groups = r._device_groups()
    assert len(groups) == 1


# -- LXMF propagation-node announces ----------------------------------------
# 2026-08-22: two "anonymous neighbour" rows were the operator's own two Pi
# relays (SKYFINGER and ELSEWHERE) heard through their THIRD identity — lxmd's
# lxmf.propagation aspect. The payload's msgpack shape is recognisable; the
# announce proves WHAT the destination is, never WHOSE machine it is.

# Real captured app_data from the two live rows.
PROP_ANNOUNCE_1 = b"\x97\xc2\xcej\x88Gz\xc3\xcd\x01\x00\xcd(\x00\x93\x10\x03\x12\x80"
PROP_ANNOUNCE_2 = b"\x97\xc2\xcej\x88T\xbb\xc3\xcd\x01\x00\xcd(\x00\x93\x10\x03\x12\x80"


def test_propagation_announce_recognised_from_real_captures():
    from monitor.registry import _is_propagation_announce
    assert _is_propagation_announce(PROP_ANNOUNCE_1)
    assert _is_propagation_announce(PROP_ANNOUNCE_2)


def test_propagation_check_refuses_everything_else():
    from monitor.registry import _is_propagation_announce
    from monitor.health_beacon import encode
    health = encode(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
                    wifi_up=True, lora_up=True, tcp_backbone_up=True,
                    local_tcp_server_up=True, wdt_armed=True, psram=True,
                    fault=False, board_id=0x3F, fw=(0, 6, 2))
    assert not _is_propagation_announce(health)
    assert not _is_propagation_announce(None)
    assert not _is_propagation_announce(b"")
    assert not _is_propagation_announce(b"\x00\x01\x02\x03garbage")
    assert not _is_propagation_announce(b"\x0bSKYFINGER!")   # an LXMF name
    # Right shape, impossible timestamp: fixarray [bool, 7, bool] is noise,
    # not a propagation node announcing in 1970.
    assert not _is_propagation_announce(b"\x93\xc2\x07\xc3")


def _mp(obj):
    """Minimal msgpack encoder for the exact shapes this test needs (bool,
    int, str, list, dict). Deliberately RNS-free: CI has no RNS module, and a
    test that hard-imports RNS.vendor.umsgpack passes on a dev venv and fails
    in CI (the 2026-08-22 red-main). The bytes are valid msgpack, so both the
    RNS decode path and the bundled byte-level fallback see the same input."""
    if isinstance(obj, bool):
        return b"\xc3" if obj else b"\xc2"
    if isinstance(obj, int):
        if 0 <= obj <= 0x7F:
            return bytes([obj])                       # positive fixint
        if obj <= 0xFFFF:
            return b"\xcd" + obj.to_bytes(2, "big")   # uint16
        return b"\xce" + obj.to_bytes(4, "big")       # uint32
    if isinstance(obj, str):
        b = obj.encode()
        return bytes([0xA0 | len(b)]) + b             # fixstr (len < 32)
    if isinstance(obj, list):
        return bytes([0x90 | len(obj)]) + b"".join(_mp(x) for x in obj)
    if isinstance(obj, dict):
        out = bytes([0x80 | len(obj)])
        for k, v in obj.items():
            out += _mp(k) + _mp(v)
        return out
    raise TypeError(obj)


def test_lookalike_prefix_is_not_a_propagation_announce():
    """P1 from the adversarial review: a payload that merely OPENS like the
    propagation announce — bool, timestamp, bool — but carries something else
    after must classify False. The shape check runs through [5], the 3-int
    triple."""
    from monitor.registry import _is_propagation_announce
    ts = 1787316090
    assert not _is_propagation_announce(
        _mp([True, ts, True, 42]))                             # too short (4)
    assert not _is_propagation_announce(
        _mp([False, ts, True, 256, 10240]))                    # too short (5)
    assert not _is_propagation_announce(
        _mp([False, ts, True, 256, 10240, [16, 3], {}]))       # [5] not 3
    assert not _is_propagation_announce(
        _mp([False, ts, True, 256, 10240, [16, "3", 18], {}])) # [5] has a str
    # ...and the real shape still passes when re-packed from its decode.
    assert _is_propagation_announce(
        _mp([False, ts, True, 256, 10240, [16, 3, 18], {}]))


def test_ingest_announce_marks_propagation_and_roundtrips():
    r = NodeRegistry()
    rec = r.ingest_announce(b"\xdd" * 16, PROP_ANNOUNCE_1, NOW)
    assert rec.is_propagation is True
    # msgpack residue must never become a display name (the "j(" ghosts).
    assert rec.announced_name == ""
    # Survives the monitoring DB round-trip; an old file without the field
    # loads as False (checked via a plain heard-neighbour record below).
    r2 = NodeRegistry.from_dict(r.to_dict())
    assert r2.nodes["dd" * 16].is_propagation is True
    plain = r.ingest_announce(b"\xee" * 16, b"", NOW)
    assert plain.is_propagation is False
    d = r.to_dict()
    for n in d["nodes"]:
        del n["is_propagation"]            # a registry file from before the field
    old = NodeRegistry.from_dict(d)
    assert old.nodes["ee" * 16].is_propagation is False


def test_propagation_row_reads_propagation_relay():
    r = NodeRegistry()
    r.ingest_announce(b"\xdd" * 16, PROP_ANNOUNCE_1, NOW)
    row = r.nodes["dd" * 16].to_dashboard(NOW)
    assert row["name"] == "Propagation relay " + "dd" * 4
    assert row["location"] == "LXMF propagation announces"
    # An operator-given name always outranks the format label.
    r.nodes["dd" * 16].name = "SKYFINGER relay"
    assert r.nodes["dd" * 16].to_dashboard(NOW)["name"] == "SKYFINGER relay"


def test_propagation_check_survives_missing_rns(monkeypatch):
    """A dev Mac without the radio stack still classifies correctly: poison the
    RNS import so the bundled minimal wire-format check must answer alone."""
    import sys
    from monitor.registry import _is_propagation_announce
    monkeypatch.setitem(sys.modules, "RNS", None)
    monkeypatch.setitem(sys.modules, "RNS.vendor", None)
    monkeypatch.setitem(sys.modules, "RNS.vendor.umsgpack", None)
    assert _is_propagation_announce(PROP_ANNOUNCE_1)
    assert _is_propagation_announce(PROP_ANNOUNCE_2)
    assert not _is_propagation_announce(b"\x0bSKYFINGER!")
    assert not _is_propagation_announce(b"\x93\xc2\x07\xc3")


def test_propagation_label_survives_consolidation():
    """P3 from the adversarial review: the VITALS rows come from devices()
    (through _device_groups/_consolidate), not from to_dashboard directly —
    the label must survive that path too."""
    r = NodeRegistry()
    r.ingest_announce(b"\xdd" * 16, PROP_ANNOUNCE_1, NOW)
    row = next(d for d in r.devices(NOW) if d["identity"] == "dd" * 16)
    assert row["name"] == "Propagation relay " + "dd" * 4
    assert row["location"] == "LXMF propagation announces"


def test_consolidation_pools_is_propagation_across_members():
    """P2: any flagged aspect-destination flags the consolidated device, even
    when a different (non-propagation) sibling leads the merge."""
    r = NodeRegistry()
    r.ingest_announce(b"\xdd" * 16, PROP_ANNOUNCE_1, NOW, identity_hash="i-p")
    r.ingest_announce(b"\xee" * 16, b"", NOW, identity_hash="i-p")
    rec = r.consolidated_record("ee" * 16, NOW)
    assert rec.is_propagation is True


# ---- last_heard_announce_at: the ping reply-watch's oracle ------------------
#
# Written ONLY by genuine (non-echo) announce/beacon ingest, so the ping's
# reply window (health_poll.heard_since) cannot be answered by a mesh
# rediscover tick or a transport replay (2026-08-22 review, C2/C3).

def test_genuine_beacon_stamps_last_heard_announce_at():
    r = NodeRegistry()
    r.ingest(HASH, beacon(uptime_s=10), NOW)
    assert r.get(HASH).last_heard_announce_at == NOW


def test_beacon_replay_does_not_stamp_last_heard_announce_at():
    r = NodeRegistry()
    r.ingest(HASH, beacon(uptime_s=10), NOW)
    r.ingest(HASH, beacon(uptime_s=10), NOW + 60)   # byte-identical = echo
    assert r.get(HASH).last_heard_announce_at == NOW


def test_bare_announce_stamps_it_and_its_replay_does_not():
    r = NodeRegistry()
    raw = bytes.fromhex(HASH)
    r.ingest_announce(raw, b"WILDNODE", NOW)
    assert r.get(HASH).last_heard_announce_at == NOW
    r.ingest_announce(raw, b"WILDNODE", NOW + 60)   # byte-identical = replay
    assert r.get(HASH).last_heard_announce_at == NOW
    r.ingest_announce(raw, b"WILDNODE-2", NOW + 90)  # new bytes = node spoke
    assert r.get(HASH).last_heard_announce_at == NOW + 90


def test_path_table_folds_never_stamp_last_heard_announce_at():
    # ingest_mesh and ingest_relay both fold the path table — table presence
    # (even with a heard timestamp) is not the node speaking NOW, and must not
    # be able to answer an in-flight ping's reply watch.
    r = NodeRegistry()
    r.ingest_mesh(_mesh(HASH, hops=2, heard=NOW), NOW)
    r.ingest_relay(HASH2, "RNodeInterface", NOW, heard=NOW)
    assert r.get(HASH).last_heard_announce_at is None
    assert r.get(HASH2).last_heard_announce_at is None


def test_last_heard_announce_at_survives_a_round_trip():
    r = NodeRegistry()
    r.ingest(HASH, beacon(uptime_s=10), NOW)
    r2 = NodeRegistry.from_dict(r.to_dict())
    assert r2.get(HASH).last_heard_announce_at == NOW


# ---- clock-step rebase (review item 4): stamps stay honest across a GPS step --
# A GPS step (offline time discipline) moves the system clock; without rebasing
# the stored epochs, a forward step makes every node look silent for the step
# size (false escalation / false "SEEN ?") and prunes real history.

def test_rebase_wall_clock_shifts_every_stamp_and_history_point():
    from monitor.history import HistoryPoint
    from monitor.registry import CommissionEvent
    reg = NodeRegistry()
    rec = reg.register(HASH, name="TRUTH")
    rec.last_seen = NOW
    rec.last_direct = NOW - 60
    rec.last_heard_announce_at = NOW
    rec.mesh_heard = NOW - 120
    rec.poll_failed_at = NOW - 30
    rec.last_echo_at = NOW - 10
    rec.share_applied_at = NOW - 200
    rec.events.append(CommissionEvent(at=NOW - 300, kind="build", summary="born"))
    reg.history.append(HASH, HistoryPoint(t=NOW - 500, rssi=-70))

    DELTA = 3 * 3600.0                     # +3h forward step
    reg.rebase_wall_clock(DELTA)

    assert rec.last_seen == NOW + DELTA
    assert rec.last_direct == NOW - 60 + DELTA
    assert rec.last_heard_announce_at == NOW + DELTA
    assert rec.mesh_heard == NOW - 120 + DELTA
    assert rec.poll_failed_at == NOW - 30 + DELTA
    assert rec.last_echo_at == NOW - 10 + DELTA
    assert rec.share_applied_at == NOW - 200 + DELTA
    assert rec.events[0].at == NOW - 300 + DELTA
    assert reg.history.series(HASH)[0].t == NOW - 500 + DELTA


def test_rebase_keeps_a_node_heard_5_min_ago_still_5_min_ago():
    reg = NodeRegistry()
    rec = reg.register(HASH, name="TRUTH")
    rec.last_seen = NOW - 300              # heard 5 min ago on the OLD clock
    DELTA = 3 * 3600.0
    reg.rebase_wall_clock(DELTA)
    # "now" is NOW+DELTA after the step; the age is still ~5 min, NOT ~3 hours
    assert rec.last_seen_hours(NOW + DELTA) == pytest.approx(300 / 3600.0, abs=1e-6)


def test_rebase_zero_delta_is_a_noop():
    reg = NodeRegistry()
    rec = reg.register(HASH)
    rec.last_seen = NOW
    reg.rebase_wall_clock(0.0)
    assert rec.last_seen == NOW


# ---- Stage 1: the SEEN field is now an Observation (source + serialization) --
# The `last_seen` bare float became an `Observation[None]` carrying WHEN the
# node was heard and HOW (its source). These tests pin the source each ingest
# path stamps, the old-file migration, and the rollback mirror — the parts of
# the migration the pre-existing SEEN tests (which only read the epoch through
# the compat property) do not exercise.

def test_seen_observation_records_the_beacon_source():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    assert r.get(HASH).seen.source == "beacon"
    assert r.get(HASH).seen.observed_at == NOW


def test_seen_observation_records_the_http_source():
    r = NodeRegistry()
    r.record_http_status(HASH, http(status="ok"), NOW)
    assert r.get(HASH).seen.source == "http"


def test_seen_observation_records_the_announce_source():
    r = NodeRegistry()
    r.ingest_announce(bytes.fromhex(HASH), b"WILDNODE", NOW)
    assert r.get(HASH).seen.source == "announce"


def test_a_route_fold_is_labelled_path_table_not_the_node_speaking():
    """A path row is a ROUTE, weaker than the node's own word — the Observation
    wears that honesty in its source so a fold can never pass for a beacon."""
    r = NodeRegistry()
    r.ingest_mesh(_mesh(HASH, heard=NOW - HOUR), NOW)
    assert r.get(HASH).seen.source == "path-table"


def test_never_heard_node_has_no_observation():
    """None IS the never state — not an Observation of zero. set_kin_roster
    seeds fleet rows before first contact."""
    r = NodeRegistry()
    r.register(HASH)
    assert r.get(HASH).seen is None
    assert r.get(HASH).last_seen is None


def test_an_old_registry_file_bare_last_seen_float_migrates_to_legacy():
    """Rollback FORWARD: a file written by the pre-Observation build has a bare
    `last_seen` float and no `seen` key. It must load — as a "legacy"-sourced
    Observation stamped at that epoch (we know WHEN, never HOW)."""
    old = {"nodes": [{"dst_hash": HASH, "name": "OLDNODE",
                      "last_seen": NOW}], "history": {}}
    back = NodeRegistry.from_dict(old)
    rec = back.get(HASH)
    assert rec.last_seen == NOW               # epoch preserved
    assert rec.seen.source == "legacy"        # honest: WHEN known, HOW not
    assert rec.last_seen_hours(NOW + HOUR) == pytest.approx(1.0)


def test_an_old_file_never_heard_stays_never():
    old = {"nodes": [{"dst_hash": HASH, "name": "N", "last_seen": None}],
           "history": {}}
    assert NodeRegistry.from_dict(old).get(HASH).seen is None


def test_a_new_file_round_trips_the_seen_source():
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    back = NodeRegistry.from_dict(r.to_dict())
    assert back.get(HASH).seen.source == "beacon"
    assert back.get(HASH).last_seen == NOW


def test_to_dict_writes_a_bare_last_seen_rollback_mirror():
    """Rollback BACKWARD: an OLDER build reading THIS file must find the plain
    epoch it expects, not a dict it would choke on — so the bare `last_seen`
    float is written alongside the `seen` Observation."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    node = r.to_dict()["nodes"][0]
    assert node["last_seen"] == NOW                       # the mirror
    assert node["seen"] == {"value": None, "observed_at": NOW,
                            "source": "beacon"}


def test_a_never_heard_node_persists_seen_as_null():
    r = NodeRegistry()
    r.register(HASH)
    node = r.to_dict()["nodes"][0]
    assert node["seen"] is None
    assert node["last_seen"] is None


def test_rebase_keeps_the_seen_source_through_a_clock_step():
    """A GPS step shifts observed_at but must NOT relabel HOW we heard it."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.rebase_wall_clock(3 * HOUR)
    rec = r.get(HASH)
    assert rec.last_seen == NOW + 3 * HOUR
    assert rec.seen.source == "beacon"        # source survives the rebase


# ---- corrupt input must not discard the whole registry --------------------
# Regression: Observation.from_dict used bracket access with no float() guard,
# and from_dict had no per-node try/except, so ONE bad node raised out of the
# loop — load() caught it and returned a FRESH EMPTY registry, silently losing
# every OTHER node's name/history/log. The old bare-float code degraded a
# garbage value to None and kept the rest; parity is restored on two layers:
# (a) a corrupt `seen` degrades that node to never-heard (Observation.from_dict);
# (b) an otherwise unbuildable row is skipped, not fatal (per-node try/except).

def _reg_with_one_corrupt_seen(bad_seen):
    good = {"dst_hash": HASH, "name": "GOODNODE", "seen":
            {"value": None, "observed_at": NOW, "source": "beacon"}}
    bad = {"dst_hash": HASH2, "name": "BADNODE", "seen": bad_seen}
    return {"nodes": [good, bad], "history": {}}


def test_a_corrupt_seen_node_keeps_the_whole_registry():
    """{}, a non-dict, and a non-numeric observed_at all degrade that node's
    seen to never-heard — the node stays, and the GOOD node is untouched (the
    fleet is NOT nuked)."""
    for bad_seen in ({}, "astring", 12345,
                     {"observed_at": "notafloat"}, {"observed_at": None}):
        reg = NodeRegistry.from_dict(_reg_with_one_corrupt_seen(bad_seen))
        assert reg.get(HASH) is not None, bad_seen           # good node survives
        assert reg.get(HASH).name == "GOODNODE"
        assert reg.get(HASH).seen.source == "beacon"
        bad = reg.get(HASH2)                                 # bad node degraded
        assert bad is not None and bad.seen is None          # never-heard
        assert bad.name == "BADNODE"


def test_a_node_with_a_corrupt_seen_still_renders():
    """No deferred crash at render time (age_at / status / to_dashboard) for a
    node whose seen degraded to never-heard."""
    reg = NodeRegistry.from_dict(
        _reg_with_one_corrupt_seen({"observed_at": "notafloat"}))
    bad = reg.get(HASH2)
    assert bad.last_seen_hours(NOW) is None
    assert bad.status(NOW) == "unknown"
    d = bad.to_dashboard(NOW)
    assert d["has_seen"] is False and d["seen_impossible"] is False


def test_an_unbuildable_row_is_skipped_not_fatal():
    """The per-node guard: a row missing its dst_hash (KeyError while building)
    is dropped, and every OTHER node still loads — instead of load() catching
    the raise and returning a FRESH EMPTY registry."""
    data = {"nodes": [
        {"dst_hash": HASH, "name": "GOODNODE"},
        {"name": "NO_HASH_ROW"},                    # missing dst_hash -> KeyError
        {"dst_hash": HASH2, "name": "ALSOGOOD"},
    ], "history": {}}
    reg = NodeRegistry.from_dict(data)
    assert reg.get(HASH) is not None and reg.get(HASH).name == "GOODNODE"
    assert reg.get(HASH2) is not None and reg.get(HASH2).name == "ALSOGOOD"
    assert len(reg.nodes) == 2                       # the bad row was skipped


# ---- Stage 2: last_direct / last_echo_at / last_heard_announce_at / mesh_heard
# The four remaining bare timestamp fields became Observations on the SAME
# machinery as `seen` (Stage 1): each keeps a compat read/write property, wears
# an honest source, rebases keeping that source, and serializes with a bare
# rollback mirror. These tests pin the parts the pre-existing behaviour tests
# (which read only the epoch through the property) do not exercise.

def test_last_direct_records_the_evidence_class_source():
    """last_direct wears the KIND of direct word it was — beacon vs http vs
    announce — not a flat 'direct'. The ingest paths thread the finer source."""
    rb = NodeRegistry()
    rb.ingest(HASH, beacon(), NOW)
    assert rb.get(HASH).last_direct_obs.source == "beacon"
    assert rb.get(HASH).last_direct == NOW

    rh = NodeRegistry()
    rh.record_http_status(HASH, http(status="ok"), NOW)
    assert rh.get(HASH).last_direct_obs.source == "http"

    ra = NodeRegistry()
    ra.ingest_announce(bytes.fromhex(HASH), b"WILDNODE", NOW)
    assert ra.get(HASH).last_direct_obs.source == "announce"


def test_a_replay_stamps_last_echo_at_with_the_echo_source():
    """A byte-identical replay is the transport echoing — sourced "echo",
    NEVER a sighting (seen/last_direct/last_heard_announce_at untouched)."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.ingest(HASH, beacon(), NOW + 200)              # byte-identical = replay
    rec = r.get(HASH)
    assert rec.last_echo_at_obs.source == "echo"
    assert rec.last_echo_at == NOW + 200
    assert rec.last_direct == NOW                     # the node did NOT speak
    assert rec.last_heard_announce_at == NOW          # oracle unmoved


def test_last_heard_announce_at_is_sourced_announce_and_genuine_only():
    """The heard_since oracle: written only by a genuine (non-replay) announce
    or beacon, sourced "announce"; a path fold never touches it."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    assert r.get(HASH).last_heard_announce_at_obs.source == "announce"
    r.ingest_mesh(_mesh(HASH2, heard=NOW - HOUR), NOW)
    assert r.get(HASH2).last_heard_announce_at is None   # a route is not a word


def test_mesh_heard_is_sourced_path_table():
    """A path row's own timestamp lands in mesh_heard, sourced "path-table" —
    a ROUTE, weaker than the node's direct word."""
    r = NodeRegistry()
    r.ingest_mesh(_mesh(HASH, heard=NOW - HOUR), NOW)
    assert r.get(HASH).mesh_heard_obs.source == "path-table"
    assert r.get(HASH).mesh_heard == NOW - HOUR


def test_stage2_fields_round_trip_with_their_sources():
    """A new file carries each Observation (source included) and restores it."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.ingest(HASH, beacon(), NOW + 200)              # add an echo
    r.ingest_mesh(_mesh(HASH, heard=NOW - HOUR), NOW + 200)
    back = NodeRegistry.from_dict(r.to_dict()).get(HASH)
    assert back.last_direct_obs.source == "beacon"
    assert back.last_echo_at_obs.source == "echo"
    assert back.last_heard_announce_at_obs.source == "announce"
    assert back.mesh_heard_obs.source == "path-table"
    assert back.last_echo_at == NOW + 200


def test_stage2_to_dict_writes_bare_rollback_mirrors():
    """Rollback BACKWARD: an OLDER build must find plain epochs beside the
    Observation dicts, not choke on the dicts."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.ingest(HASH, beacon(), NOW + 200)
    node = r.to_dict()["nodes"][0]
    assert node["last_direct"] == NOW                 # bare mirrors
    assert node["last_echo_at"] == NOW + 200
    assert node["last_heard_announce_at"] == NOW
    assert node["last_direct_obs"]["source"] == "beacon"
    assert node["last_echo_at_obs"]["source"] == "echo"


def test_an_old_file_bare_stage2_floats_migrate_to_legacy():
    """Rollback FORWARD: an old file's bare floats (no `_obs` keys) load as
    "legacy"-sourced Observations — WHEN known, HOW not — and the epochs stand."""
    old = {"nodes": [{"dst_hash": HASH, "name": "OLDNODE",
                      "last_direct": NOW, "mesh_heard": NOW - HOUR,
                      "last_echo_at": NOW - 10,
                      "last_heard_announce_at": NOW}], "history": {}}
    rec = NodeRegistry.from_dict(old).get(HASH)
    assert rec.last_direct == NOW and rec.last_direct_obs.source == "legacy"
    assert rec.mesh_heard == NOW - HOUR
    assert rec.last_echo_at == NOW - 10
    assert rec.last_heard_announce_at == NOW
    assert rec.last_direct_hours(NOW + HOUR) == pytest.approx(1.0)


def test_a_corrupt_stage2_obs_degrades_that_field_not_the_fleet():
    """A garbage `_obs` entry degrades just that field to None, keeps the node,
    and never wipes the registry (the from_dict corruption promise)."""
    data = {"nodes": [
        {"dst_hash": HASH, "name": "GOODNODE",
         "last_direct_obs": {"observed_at": "notafloat"},
         "last_echo_at_obs": "astring",
         "mesh_heard_obs": {},
         "last_heard_announce_at_obs": 12345},
    ], "history": {}}
    rec = NodeRegistry.from_dict(data).get(HASH)
    assert rec is not None and rec.name == "GOODNODE"
    assert rec.last_direct is None and rec.last_echo_at is None
    assert rec.mesh_heard is None and rec.last_heard_announce_at is None
    assert rec.last_direct_hours(NOW) is None          # no deferred crash


def test_rebase_keeps_stage2_sources_through_a_clock_step():
    """A GPS step shifts every observed_at but must NOT relabel HOW we heard —
    the reason these four were pulled out of the bare-setter rebase path."""
    r = NodeRegistry()
    r.ingest(HASH, beacon(), NOW)
    r.ingest(HASH, beacon(), NOW + 200)              # echo
    r.ingest_mesh(_mesh(HASH, heard=NOW - HOUR), NOW + 200)
    r.rebase_wall_clock(3 * HOUR)
    rec = r.get(HASH)
    assert rec.last_direct == NOW + 3 * HOUR
    assert rec.last_direct_obs.source == "beacon"     # source survives
    assert rec.last_echo_at_obs.source == "echo"
    assert rec.mesh_heard_obs.source == "path-table"
    assert rec.mesh_heard == NOW - HOUR + 3 * HOUR


def test_chats_heard_names_become_the_rows_announced_name():
    """An LXMF announce carries its display name in a msgpack array, which
    _printable_name rightly refuses — so the operator's phone sat in VITALS as
    "Neighbour 5a150015" while CHAT called it 5ugAv (2026-10-02). The chat
    service decodes it; the registry takes the hand-over by hash."""
    r = NodeRegistry()
    phone = b"\xdf\x26" + b"\x11" * 14
    rec = r.ingest_announce(phone, b"\x92\xa55ugAv\x08", NOW)     # msgpack [name, cost]
    assert rec.announced_name == ""                               # refused, as it must be
    assert "Neighbour df26" in rec.to_dashboard(NOW)["name"]
    assert r.set_peer_names({phone.hex(): "5ugAv", "zz": "", "": "x"}) == 1
    assert rec.announced_name == "5ugAv"
    assert rec.to_dashboard(NOW)["name"] == "5ugAv"
    # the operator's own name always wins, and residue is still refused
    rec.name = "Marnie's phone"
    assert rec.to_dashboard(NOW)["name"] == "Marnie's phone"
    assert r.set_peer_names({phone.hex(): "j("}) == 0
    # a hash heard AFTER the hand-over gets the name on its first announce
    later = b"\xab" * 16
    r.set_peer_names({later.hex(): "Late phone"})
    assert r.ingest_announce(later, b"\x92\xaaLate phone\x08", NOW).announced_name == "Late phone"
