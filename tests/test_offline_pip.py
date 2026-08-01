"""Finding pip on a node that ships without it.

Raspberry Pi OS Lite has no pip3 at all — birthing HOPE (2026-08-01) stopped
dead on it. But Debian ships pip's OWN wheel at /usr/share/python-wheels for
python3-venv's benefit, and pip runs straight out of that wheel. So a fully
offline node can bootstrap pip with no apt, no internet, no extra carried file.
"""

import pytest

from workflows.build import _ensure_pip, PIP_WHEEL_GLOB


class FakeConn:
    """Answers shell probes from a table; records what was run."""

    def __init__(self, table):
        self.table = table
        self.ran = []

    def run(self, cmd, timeout=None):
        self.ran.append(cmd)
        for pattern, result in self.table.items():
            if pattern in cmd:
                return result
        return (1, "", "not found")


class FakeWF:
    def __init__(self, table):
        self.connection = FakeConn(table)
        self.pip_cmd = "pip3"

    def priv(self, cmd):
        return "sudo -n " + cmd


WHEEL = "/usr/share/python-wheels/pip-25.1.1-py3-none-any.whl"


def test_an_installed_pip3_is_used_directly():
    wf = FakeWF({"command -v pip3": (0, "/usr/bin/pip3", "")})
    ok, _ = _ensure_pip(wf)
    assert ok and wf.pip_cmd == "pip3"


def test_python_m_pip_is_accepted_when_the_binary_is_absent():
    wf = FakeWF({"python3 -m pip --version": (0, "pip 25.1.1", "")})
    ok, _ = _ensure_pip(wf)
    assert ok and wf.pip_cmd == "python3 -m pip"


def test_pip_is_bootstrapped_from_the_images_own_wheel_when_nothing_is_installed():
    """The HOPE case: no pip3, no internet, but the wheel is already there."""
    wf = FakeWF({
        "ls /usr/share/python-wheels/pip-*.whl": (0, WHEEL + "\n", ""),
        f"python3 {WHEEL}/pip --version": (0, "pip 25.1.1", ""),
    })
    ok, note = _ensure_pip(wf)
    assert ok
    assert wf.pip_cmd == f"python3 {WHEEL}/pip"
    assert "offline" in note


def test_the_offline_wheel_is_tried_BEFORE_apt():
    """Reaching for apt on an offline node burns ~17 minutes of timeouts before
    failing, and a field node is offline by definition."""
    wf = FakeWF({
        "ls /usr/share/python-wheels/pip-*.whl": (0, WHEEL + "\n", ""),
        f"python3 {WHEEL}/pip --version": (0, "pip 25.1.1", ""),
    })
    _ensure_pip(wf)
    assert not any("apt-get" in c for c in wf.connection.ran), \
        "apt was contacted even though the offline wheel was available"


def test_apt_is_still_the_last_resort_when_there_is_no_wheel():
    wf = FakeWF({
        "apt-get install -y python3-pip": (0, "", ""),
        "command -v pip3": (1, "", ""),
    })
    _ensure_pip(wf)
    assert any("apt-get install -y python3-pip" in c for c in wf.connection.ran)


def test_a_node_with_no_pip_anywhere_fails_with_a_message_naming_all_three_routes():
    wf = FakeWF({})
    ok, msg = _ensure_pip(wf)
    assert ok is False
    assert "none installed" in msg
    assert PIP_WHEEL_GLOB in msg
    assert "apt" in msg


def test_a_present_but_broken_wheel_does_not_get_used():
    """The file existing is not proof pip can run from it."""
    wf = FakeWF({
        "ls /usr/share/python-wheels/pip-*.whl": (0, WHEEL + "\n", ""),
        f"python3 {WHEEL}/pip --version": (1, "", "bad zip"),
    })
    ok, _ = _ensure_pip(wf)
    assert wf.pip_cmd != f"python3 {WHEEL}/pip"


def test_install_steps_use_the_resolved_pip_not_a_hardcoded_pip3():
    """The bug this whole change exists to prevent: a node whose pip is
    'python3 <wheel>/pip' would silently run 'pip3' and fail."""
    import inspect
    from workflows import build
    src = inspect.getsource(build)
    assert '"pip3 install' not in src and "f\"pip3 install" not in src
    assert src.count("{wf.pip_cmd} install") >= 4
