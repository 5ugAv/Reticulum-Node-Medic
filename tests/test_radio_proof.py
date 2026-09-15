"""Birth must prove the RADIO, not just the cable (task #77).

Operator, 2026-08-06: "there should also be a test during that birth where it
tests the lora to see if it can find it over the lora as well."

Birth proves a node is CONFIGURED. Only hearing it proves it is REACHABLE, and
that is what the node is for. Every test here is a way the old, missing check
would have let a deaf node onto a pole.
"""

import types

from workflows import radio_proof as rp


def _result(reachable=True, rssi=None, snr=None):
    beacon = types.SimpleNamespace(rssi=rssi, snr=snr)
    return types.SimpleNamespace(reachable=reachable, beacon=beacon)


def _prove(iface="RNodeInterface[Jonesey]", reachable=True, dropped=None,
           rssi=None, snr=None, **kw):
    def drop(h):
        if dropped is not None:
            dropped.append(h)
    return rp.prove_radio(
        b"\x01\x02", drop_cached_path=drop,
        poll=lambda h: _result(reachable, rssi, snr),
        interface_for=lambda h: iface, now=lambda: 1000.0, **kw)


# --- the happy path ---------------------------------------------------------

def test_hearing_it_over_a_lora_interface_is_proof():
    p = _prove(node_name="faith", rssi=-91.5, snr=7.0)
    assert p.heard is True
    assert p.rssi == -91.5 and p.snr == 7.0
    assert "Heard faith over the radio" in p.summary


def test_the_certificate_records_evidence_not_a_promise():
    f = _prove(node_name="faith", rssi=-91.5).cert_fields()
    assert f["radio_verified"] is True
    assert "RNodeInterface" in f["radio_interface"]
    assert f["radio_rssi"] == -91.5
    assert f["radio_checked_at"] == 1000.0


# --- CACHED PATHS LIE -------------------------------------------------------
# first-range-test-faith cost an evening to this: a reachability check answered
# through a stale cached path and reported a working radio that was not working.

def test_the_cached_path_is_dropped_BEFORE_asking():
    dropped = []
    _prove(dropped=dropped)
    assert dropped == [b"\x01\x02"], "the cached path was not dropped first"


# --- THE FLATTERING CASE ----------------------------------------------------
# It answers — over the USB cable that is still plugged in. The radio is exactly
# as unproven as before, but a naive check calls it a pass.

def test_answering_over_the_cable_is_NOT_proof():
    p = _prove(iface="AutoInterface")
    assert p.heard is False
    assert p.answered_off_air is True
    assert "proves the cable, which we already knew" in p.summary


def test_the_cable_case_says_to_unplug_and_retry_FIRST():
    p = _prove(iface="AutoInterface")
    assert "Unplug the USB cable" in p.checks[0]


def test_a_tcp_interface_is_not_proof_either():
    """Reaching it through somebody's hub says nothing about its LoRa modem."""
    assert _prove(iface="TCPClientInterface[hub]").heard is False


def test_an_unknown_interface_fails_CLOSED():
    """The operator is about to walk away and trust this. An interface we do not
    recognise must not be allowed to stand in for a radio."""
    assert rp.is_over_air("SomethingNew") is False
    assert rp.is_over_air("") is False
    assert _prove(iface="MysteryInterface").heard is False


def test_a_real_rnode_interface_IS_recognised():
    for name in ("RNodeInterface[Jonesey]", "rnode0", "LoRa Interface"):
        assert rp.is_over_air(name) is True, name


# --- failure is informative, never fatal ------------------------------------

def test_not_being_heard_is_a_RESULT_not_an_exception():
    p = _prove(reachable=False, node_name="faith")
    assert p.heard is False
    # Wording changed 2026-09-09: "did not answer" reads as a verdict on the
    # node, but it is only a verdict on this moment, and it lands in a
    # permanent record. See tests/test_radio_proof_settle.py.
    assert "has not answered over the radio" in p.summary
    assert p.checks, "a failure with no advice is just bad news"


def test_a_broken_transport_does_not_fail_the_whole_birth():
    """The birth has otherwise succeeded. An exception in the radio check must
    not throw that away."""
    def boom(h):
        raise RuntimeError("no rnsd")
    p = rp.prove_radio(b"\x01", drop_cached_path=lambda h: None, poll=boom,
                       interface_for=lambda h: "", now=lambda: 1.0)
    assert p.heard is False
    assert "Could not run the radio check" in p.summary


def test_a_failure_to_drop_the_path_still_runs_the_check():
    """A weaker test is better than no test."""
    def boom(h):
        raise RuntimeError("rnpath missing")
    p = rp.prove_radio(b"\x01", drop_cached_path=boom,
                       poll=lambda h: _result(True),
                       interface_for=lambda h: "RNodeInterface[J]",
                       now=lambda: 1.0)
    assert p.heard is True


def test_the_antenna_warnings_come_first_and_name_the_damage():
    """Never transmit without an antenna — the one check whose absence breaks
    hardware rather than just the test."""
    p = _prove(reachable=False)
    assert "antenna" in p.checks[0].lower()
    assert "damage it permanently" in p.checks[1]


def test_mismatched_radio_params_are_named_as_a_cause():
    """A node on a different frequency is invisible, not broken — and that is
    the failure most likely to be misread as a dead board."""
    p = _prove(reachable=False)
    assert any("frequency" in c for c in p.checks)


def test_an_unheard_node_never_claims_verification_on_the_certificate():
    f = _prove(reachable=False).cert_fields()
    assert f["radio_verified"] is False
    assert f["radio_rssi"] is None


# --- reading the interface out of the real path table ------------------------
# This attribution is what the whole test rests on. rnpath -t --json carries an
# explicit "interface" per destination — the shape was verified against the
# live medic; the hashes below are synthetic.

REAL_JSON = ('[{"hash": "5566778899aabbccddeeff0011223344", "hops": 0, '
             '"interface": "LocalInterface[rns/default]"}, '
             '{"hash": "5a0b000baabb", "hops": 1, '
             '"interface": "RNodeInterface[RNode LoRa Interface]"}]')


def test_the_interface_is_read_for_the_right_destination():
    assert rp.path_interface(REAL_JSON, "5a0b000baabb") == \
        "RNodeInterface[RNode LoRa Interface]"
    assert rp.path_interface(REAL_JSON, "5566778899aabbccddeeff0011223344") == \
        "LocalInterface[rns/default]"


def test_a_destination_not_in_the_table_reads_as_unknown():
    assert rp.path_interface(REAL_JSON, "deadbeef") == ""


def test_unparseable_path_output_is_not_proof():
    """A reading we could not take is not a reading that passed."""
    for junk in ("", "not json", "{}", None):
        assert rp.path_interface(junk, "abc") == ""
        assert rp.is_over_air(rp.path_interface(junk, "abc")) is False


def test_the_medics_own_LOCAL_interface_is_not_proof():
    """Every entry in the live table today is LocalInterface — the medic's own
    destinations. None of them proves a radio."""
    assert rp.is_over_air("LocalInterface[rns/default]") is False


def test_live_probes_runs_rnpath_through_the_given_shell():
    calls = []
    probes = rp.live_probes(lambda cmd: (calls.append(cmd) or REAL_JSON))
    probes["drop_cached_path"]("5a0b000baabb")
    probes["interface_for"]("5a0b000baabb")
    assert any("rnpath --drop 5a0b000baabb" in c for c in calls)
    assert any("rnpath -t --json" in c for c in calls)


def test_live_probes_WAITS_for_a_fresh_path():
    """_ping_node learned this the hard way: read the table straight after
    dropping and every node reports 'not answering', healthy or not, because no
    fresh path has resolved yet. rnpath -w does the request AND the wait."""
    calls = []
    rp.live_probes(lambda cmd: (calls.append(cmd) or ""))["poll"]("abc")
    assert any("rnpath -w" in c for c in calls), "no wait — this will misreport"


# --- the birth flow wiring --------------------------------------------------

def test_the_proof_runs_BEFORE_the_certificate_is_saved():
    src = open("ui/screens/birth_screen.py").read()
    add = src.index("self._add_radio_proof(cert)")
    save = src.index("self._saved_cert_id = save_cert(cert)")
    assert add < save, "the certificate is saved before the radio is checked"


def test_a_node_with_no_mesh_address_reads_NOT_APPLICABLE():
    """Operator, 2026-08-07: the radio fields on an RNode should read 'Not
    Applicable'. A plain RNode has no Reticulum identity of its own, so there is
    nothing addressable to hear — that is not a test it failed, and a BLANK is
    worse than either, because the reader has to guess whether the check errored,
    was skipped, or was forgotten."""
    src = open("ui/screens/birth_screen.py").read()
    fn = src[src.index("def _add_radio_proof"):src.index("def _register_kin")]
    assert "RadioProof.na(" in fn
    assert "Not applicable" in fn


def test_a_failed_radio_check_never_fails_the_birth():
    src = open("ui/screens/birth_screen.py").read()
    fn = src[src.index("def _add_radio_proof"):src.index("def _register_kin")]
    assert "except Exception:" in fn


def test_the_verdict_is_shown_before_the_certificate_fields():
    """An operator about to put this on a pole should not have to read a
    key/value list to find out whether it was ever heard."""
    src = open("ui/screens/birth_screen.py").read()
    verdict = src.index("proof.summary")
    # The heading is tr()-wrapped since 2026-09-15; the ORDER is the law here.
    fields = src.index('_line(tr("Birth certificate:")')
    assert verdict < fields


# --- "Not applicable" must not be mistaken for a pass ------------------------

def test_not_applicable_says_so_on_every_field():
    f = rp.RadioProof.na("nothing to call").cert_fields()
    for k in ("radio_verified", "radio_interface", "radio_rssi", "radio_snr",
              "radio_checked_at"):
        assert f[k] == rp.NOT_APPLICABLE, k


def test_THE_TRUTHY_TRAP():
    """'Not applicable' is a non-empty string, so `if cert["radio_verified"]:`
    is TRUE for it. That would report a node as radio-verified precisely when no
    radio test was ever possible — on the one document whose job is to state
    what was actually checked. radio_was_verified() is the only safe reader."""
    na = rp.RadioProof.na("x").cert_fields()
    assert bool(na["radio_verified"]) is True          # the trap is real
    assert rp.radio_was_verified(na) is False          # and this closes it


def test_radio_was_verified_is_true_ONLY_for_a_real_pass():
    assert rp.radio_was_verified(_prove(node_name="f").cert_fields()) is True
    assert rp.radio_was_verified(_prove(reachable=False).cert_fields()) is False
    assert rp.radio_was_verified(_prove(iface="AutoInterface").cert_fields()) is False
    assert rp.radio_was_verified({}) is False
    assert rp.radio_was_verified(None) is False


def test_not_applicable_is_not_the_same_as_not_heard():
    na = rp.RadioProof.na("nothing to call")
    no = _prove(reachable=False)
    assert na.not_applicable is True and no.not_applicable is False
    assert na.checks == [], "there is nothing to go and check"
    assert no.checks, "a real failure must still advise"


def test_the_screen_does_not_colour_not_applicable_as_a_problem():
    """Amber would send the operator looking for a fault that does not exist."""
    src = open("ui/screens/birth_screen.py").read()
    blk = src[src.index("proof = getattr(self, \"_radio_proof\""):]
    blk = blk[:blk.index("Birth certificate:")]
    assert "proof.not_applicable" in blk
    assert "text_secondary" in blk


def test_a_pi_build_records_not_tested_rather_than_failed():
    """Y2K8 and SolarLove, 2026-08-10: every Pi certificate read "did not answer
    over the radio". On this path the radio is attached AFTER the build — it has
    to be, the medic's cable holds the Pi's only USB-A until then — so the check
    ran when the answer was guaranteed to be no.

    A false negative on a birth certificate is the same offence as a false
    positive, and reads worse: the operator is told their good node is deaf."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/birth_screen.py", "_add_radio_proof")
    assert '_last_type", "") == "pi_rnode"' in src
    assert "RadioProof.na(" in src
    # and it must say where the real answer is
    assert "On @ 1.8kbps" in src, "point at the radio's own screen"
    # the na() branch has to come BEFORE the probe that cannot succeed
    assert src.index("pi_rnode") < src.index("prove_radio")
