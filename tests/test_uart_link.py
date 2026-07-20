"""Medic-side UART login driver — drive a node's serial getty (login -> shell),
tested against a scripted fake getty (no hardware, virtual clock so no waiting)."""

from provisioning.uart_link import drive_login, _SHELL_TOKEN


class Clock:
    """Virtual monotonic clock — advances a step per call so timeouts resolve
    instantly instead of sleeping real seconds."""
    def __init__(self, step=0.1):
        self.t, self.step = 0.0, step

    def __call__(self):
        self.t += self.step
        return self.t


class FakeGetty:
    """A scripted Linux serial console: emits a login prompt, accepts user then
    password, then behaves as a shell (echoes commands). Responses are queued and
    returned by read()."""

    def __init__(self, user="everywhere", password="everywhere",
                 already_shell=False, bad_password=False):
        self.user, self.password, self.bad = user, password, bad_password
        self.outq = []
        self.state = "shell" if already_shell else "login"
        if not already_shell:
            self.outq.append("\r\nraspberrypi login: ")

    def write(self, data):
        d = data.strip()
        if not d:
            return                                     # a bare wake newline
        if self.state == "login":
            # any non-empty line at 'login:' is taken as a username
            self.state = "password"
            self.outq.append("Password: ")
        elif self.state == "password":
            if self.bad:
                self.state = "login"
                self.outq.append("\r\nLogin incorrect\r\nraspberrypi login: ")
            else:
                self.state = "shell"
                self.outq.append("\r\npi@everywhere:~$ ")
        elif self.state == "shell" and d.startswith("echo "):
            token = d.split(" ", 1)[1]
            self.outq.append(f"{token}\r\npi@everywhere:~$ ")

    def read(self, _timeout):
        return self.outq.pop(0) if self.outq else ""


def test_login_success_from_fresh_prompt():
    g = FakeGetty()
    ok, transcript = drive_login(g.write, g.read, "everywhere", "everywhere",
                                 timeout=20.0, now=Clock())
    assert ok is True
    assert g.state == "shell"


def test_login_when_already_at_a_shell():
    g = FakeGetty(already_shell=True)
    ok, _ = drive_login(g.write, g.read, "everywhere", "everywhere",
                        timeout=20.0, now=Clock())
    assert ok is True                                 # confirmed via echo token


def test_login_fails_on_bad_password():
    g = FakeGetty(bad_password=True)
    ok, _ = drive_login(g.write, g.read, "everywhere", "wrongpw",
                        timeout=20.0, now=Clock())
    assert ok is False


def test_login_fails_when_nothing_answers():
    class Dead:
        def write(self, data): pass
        def read(self, _t): return ""
    d = Dead()
    ok, _ = drive_login(d.write, d.read, "u", "p", timeout=5.0, now=Clock())
    assert ok is False


def test_does_not_blind_probe_a_waiting_login_prompt():
    # A getty at 'login:' must NOT receive the echo-token as a username before the
    # real login runs — the driver waits for the prompt and sends the username.
    g = FakeGetty()
    sent = []
    orig_write = g.write
    def spy(data):
        sent.append(data.strip())
        orig_write(data)
    drive_login(spy, g.read, "everywhere", "everywhere", timeout=20.0, now=Clock())
    # the echo-token is only ever sent AFTER the username (never as the username)
    first_nonempty = next((s for s in sent if s), "")
    assert _SHELL_TOKEN not in first_nonempty
    assert "everywhere" in sent                        # username was sent
