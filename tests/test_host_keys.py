"""SSH host-key pinning (audit C1): parsing, fingerprint, pin file, and the
SSHConnection verify-vs-accept-new options."""

import base64
import hashlib

from provisioning import host_keys
from transport.connection import SSHConnection


# a self-consistent ed25519-shaped key line (raw bytes -> base64 blob)
_RAW = b"\x00\x00\x00\x0bssh-ed25519\x00\x00\x00 " + b"k" * 32
_BLOB = base64.b64encode(_RAW).decode()
LINE = f"ssh-ed25519 {_BLOB} node@medic"
HOSTLINE = f"10.55.0.1 ssh-ed25519 {_BLOB}"      # with a leading host field


def test_key_blob_parses_with_or_without_host():
    assert host_keys.key_blob(LINE) == ("ssh-ed25519", _BLOB)
    assert host_keys.key_blob(HOSTLINE) == ("ssh-ed25519", _BLOB)
    assert host_keys.key_blob("garbage no key here") == (None, None)


def test_fingerprint_matches_openssh_algorithm():
    expected = "SHA256:" + base64.b64encode(
        hashlib.sha256(_RAW).digest()).decode().rstrip("=")
    assert host_keys.fingerprint(LINE) == expected
    assert host_keys.fingerprint("no key") is None


def test_fingerprint_changes_with_the_key():
    other = f"ssh-ed25519 {base64.b64encode(_RAW + b'x').decode()}"
    assert host_keys.fingerprint(LINE) != host_keys.fingerprint(other)


def test_matches_ignores_host_and_comment():
    assert host_keys.matches(LINE, HOSTLINE)                 # same key, diff host
    assert not host_keys.matches(LINE, f"ssh-ed25519 {base64.b64encode(b'zzzz').decode()}")


def test_pick_key_prefers_ed25519():
    scan = (f"10.55.0.1 ssh-rsa {base64.b64encode(b'rsablob').decode()}\n"
            f"# comment\n"
            f"10.55.0.1 ssh-ed25519 {_BLOB}\n")
    assert host_keys.key_blob(host_keys.pick_key(scan))[0] == "ssh-ed25519"


def test_known_hosts_entry_handles_port():
    assert host_keys.known_hosts_entry("h", LINE) == f"h ssh-ed25519 {_BLOB}"
    assert host_keys.known_hosts_entry("h", LINE, port=2222) == \
        f"[h]:2222 ssh-ed25519 {_BLOB}"


def test_pin_write_read_and_replace(tmp_path):
    path = str(tmp_path / "known_hosts")
    assert not host_keys.is_pinned("10.55.0.1", path)
    assert host_keys.write_known_hosts("10.55.0.1", LINE, path)
    assert host_keys.is_pinned("10.55.0.1", path)
    assert host_keys.matches(host_keys.pinned_line("10.55.0.1", path), LINE)
    # re-pinning the same host replaces (no duplicate line)
    host_keys.write_known_hosts("10.55.0.1", LINE, path)
    with open(path) as f:
        assert sum(1 for ln in f if ln.strip()) == 1


# -- SSHConnection host-key options -----------------------------------------

def test_connection_verifies_when_pinned():
    c = SSHConnection(host="10.55.0.1", user="pi", known_hosts="/pin/known_hosts")
    argv = c._argv("echo hi")
    assert "UserKnownHostsFile=/pin/known_hosts" in argv
    assert "StrictHostKeyChecking=yes" in argv
    assert "StrictHostKeyChecking=accept-new" not in argv


def test_connection_accept_new_when_unpinned():
    c = SSHConnection(host="10.55.0.1", user="pi")   # no known_hosts
    argv = c._argv("echo hi")
    assert "StrictHostKeyChecking=accept-new" in argv
    assert not any("UserKnownHostsFile" in a for a in argv)


# -- tamper detection (a CHANGED pinned key) --------------------------------

def test_host_key_changed_recognises_mismatch():
    assert host_keys.host_key_changed(
        "@@@@@@\nWARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!\n")
    assert host_keys.host_key_changed("Host key verification failed.")
    assert not host_keys.host_key_changed("ssh: connect to host ... Connection refused")
    assert not host_keys.host_key_changed("")


def _conn(code, err):
    return SSHConnection(host="10.55.0.1", user="pi", retry_count=1,
                         sleep=lambda _s: None,
                         runner=lambda argv, timeout: (code, "", err))


def test_check_reachable_ok():
    assert _conn(0, "").check_reachable() == (True, "ok")


def test_check_reachable_flags_changed_key_as_tamper():
    c = _conn(255, "REMOTE HOST IDENTIFICATION HAS CHANGED!")
    assert c.check_reachable() == (False, "host_key_changed")


def test_check_reachable_plain_failure_is_unreachable():
    c = _conn(255, "ssh: connect to host 10.55.0.1 port 22: Connection timed out")
    assert c.check_reachable() == (False, "unreachable")


# --- the identity a new card destroys -------------------------------------

def test_forget_host_key_removes_only_that_host(tmp_path):
    from provisioning import host_keys
    p = tmp_path / "known_hosts"
    p.write_text("10.55.0.1 ssh-ed25519 AAAAold\n"
                 "other.local ssh-ed25519 AAAAkeep\n")
    gone = host_keys.forget_host_key("10.55.0.1", paths=[str(p)])
    assert gone == 1
    left = p.read_text()
    assert "AAAAold" not in left and "AAAAkeep" in left


def test_it_cleans_the_ORDINARY_known_hosts_too(tmp_path):
    """The bug was in ~/.ssh/known_hosts, not the pinned file. Nothing in this
    project ever wrote there deliberately — `accept-new` did, behind our backs —
    so nothing thought to clean it."""
    from provisioning import host_keys
    files = host_keys.known_hosts_files(pinned=str(tmp_path / "pinned"))
    assert any(f.endswith("/.ssh/known_hosts") for f in files)
    assert any("reticulum-node-medic" in f or f.endswith("pinned") for f in files)


def test_a_new_card_forgets_the_cable_address_and_the_name(tmp_path):
    """Every cable-born node answers on 10.55.0.1, so the stale key breaks the
    NEXT birth, not the one that made it. A rebirth reuses the name too."""
    from provisioning import host_keys
    p = tmp_path / "kh"
    p.write_text("10.55.0.1 ssh-ed25519 AAAAcable\n"
                 "y2k8 ssh-ed25519 AAAAshort\n"
                 "y2k8.local ssh-ed25519 AAAAmdns\n"
                 "someone.else ssh-ed25519 AAAAkeep\n")
    gone = host_keys.forget_reimaged_node("y2k8", paths=[str(p)])
    assert gone == 3
    assert p.read_text().strip() == "someone.else ssh-ed25519 AAAAkeep"


def test_a_missing_known_hosts_is_not_an_error(tmp_path):
    from provisioning import host_keys
    assert host_keys.forget_reimaged_node("x", paths=[str(tmp_path / "nope")]) == 0


def test_the_imager_forgets_it_at_the_moment_it_writes_the_card():
    """Not at connect time — at the moment the old identity ceases to exist.
    Anywhere later and the failure has already happened."""
    from tests.srcutil import func_source
    src = func_source("ui/screens/pi_imager_screen.py", "_flash")
    assert "forget_reimaged_node" in src
    assert src.index("if ok:") < src.index("forget_reimaged_node"), \
        "only after a card was actually written"


def test_hashed_entries_are_removed_by_ssh_keygen_not_a_line_scan(tmp_path):
    """OpenSSH hashes known_hosts by default — the real file on the medic is all
    `|1|...` — so `line.split()[0] == host` matches nothing and returns a
    confident zero. That is what the first version of this did, and it reported
    success while removing nothing."""
    from provisioning import host_keys
    p = tmp_path / "kh"
    p.write_text("|1|abc=|def= ssh-ed25519 AAAAhashed\n")
    calls = []
    # the runner stands in for ssh-keygen: prove we DELEGATE, and to the right file
    host_keys.forget_host_key("10.55.0.1", paths=[str(p)],
                              runner=lambda argv: calls.append(argv))
    assert calls, "a hashed file cannot be cleaned by string matching"
    assert calls[0][:2] == ["ssh-keygen", "-f"]
    assert calls[0][1:] == ["-f", str(p), "-R", "10.55.0.1"]


def test_a_reimaged_node_forgets_its_old_ADDRESSES_too():
    """ssh stores a host key under the name AND under the address, and clearing
    one leaves the other. A rebuilt skyfinger cleared cleanly by name and still
    failed, because the PREVIOUS node's key sat under 192.168.1.2 — three
    entries, found only by asking what the name resolved to (2026-08-11).

    At card-write time the name usually still points at the node being
    replaced, which is exactly the one whose key must go."""
    from provisioning import host_keys
    asked = []

    def fake_resolver(name):
        asked.append(name)
        return ["192.168.1.2"] if name.endswith(".local") else []

    cleared = []
    real = host_keys.forget_host_key
    host_keys.forget_host_key = lambda h, paths=None: cleared.append(h) or 1
    try:
        host_keys.forget_reimaged_node("skyfinger", resolver=fake_resolver)
    finally:
        host_keys.forget_host_key = real
    assert "10.55.0.1" in cleared, "the cable address every node inherits"
    assert "skyfinger" in cleared and "skyfinger.local" in cleared
    assert "192.168.1.2" in cleared, "and the address that name resolves to"


def test_it_does_not_clear_the_same_address_twice():
    from provisioning import host_keys
    cleared = []
    real = host_keys.forget_host_key
    host_keys.forget_host_key = lambda h, paths=None: cleared.append(h) or 0
    try:
        host_keys.forget_reimaged_node("n", resolver=lambda _n: ["10.55.0.1"])
    finally:
        host_keys.forget_host_key = real
    assert cleared.count("10.55.0.1") == 1
