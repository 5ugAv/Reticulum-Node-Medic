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
