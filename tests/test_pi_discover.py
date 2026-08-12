

# --- a re-imaged node is a NEW identity; the probe must not trust-check -----

def test_probe_argv_is_host_key_blind():
    """The gate's liveness probe asks 'are you up?', not 'are you who you were?'
    — a re-imaged card legitimately rotates its host key, and the stale entry
    blocked the gate twice on 2026-08-12 (nodes 'soon' and 'ttt'), stranding
    the walkthrough at 'looking for the Pi' until keys were cleared by hand
    over SSH. The build's own connection still verifies; this probe must not."""
    from provisioning.pi_discover import _probe_argv
    argv = _probe_argv("192.168.1.2", "pi")
    joined = " ".join(argv)
    assert "UserKnownHostsFile=/dev/null" in joined
    assert "StrictHostKeyChecking=no" in joined
    assert "BatchMode=yes" in joined
    assert "cat /proc/uptime" in joined


def test_imaging_forgets_the_old_identitys_keys():
    """save_cert retires the old certificate at the one choke point every path
    goes through; record_imaged_pi is that choke point for host keys. Writing
    a card MAKES a new identity — keeping the old key just schedules a
    verification failure for later."""
    from provisioning import pi_discover as pd
    calls = []
    pd.forget_node_keys("ttt", _run=lambda argv, timeout=8: calls.append(argv) or "")
    flat = [" ".join(a) for a in calls]
    assert any("-R ttt.local" in c for c in flat)
    assert any("-R ttt" in c and ".local" not in c.split("-R ")[1].split()[0] for c in flat)
    assert any("-R 10.55.0.1" in c for c in flat)
    # both stores: the default known_hosts AND the medic's pinned file
    assert any("-f" in c for c in flat)


def test_record_imaged_pi_calls_the_forgetting(tmp_path):
    from tests.srcutil import func_source
    src = func_source("provisioning/pi_discover.py", "record_imaged_pi")
    assert "forget_node_keys" in src
