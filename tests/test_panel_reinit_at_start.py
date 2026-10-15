"""The kiosk re-initialises the touchscreen panel at every start (2026-10-15):
the kernel's one initialisation at boot sometimes comes up dark or scrambled
and nothing can tell, so the medic applies the proven cure itself, before its
first frame, and tags the journal so Self Diagnose can tell its own re-inits
from cures done by hand."""
from tests.srcutil import src


class _Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        import types
        if argv[0] == "wlr-randr" and len(argv) == 1:
            return types.SimpleNamespace(returncode=0, stdout="DSI-2 \"(null)\"\n  Enabled: yes\n",
                                         stderr="")
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")


def test_reinit_at_start_runs_the_cure_and_tags_the_journal():
    from provisioning import screen_fix as sf
    r = _Runner()
    logged = []
    assert sf.reinit_at_start(runner=r, sleep=lambda s: None, log=logged.append) is True
    assert ["wlr-randr", "--output", "DSI-2", "--off"] in r.calls
    assert ["wlr-randr", "--output", "DSI-2", "--on"] in r.calls
    tag = [c for c in r.calls if c[:3] == ["logger", "-t", sf.JOURNAL_TAG]]
    assert tag and "re-initialised at start" in tag[0][3]
    assert logged and logged[0].startswith("[panel] re-initialised at start")


def test_reinit_at_start_never_raises_and_says_so():
    from provisioning import screen_fix as sf

    def boom(argv, **kw):
        raise OSError("no compositor")
    logged = []
    assert sf.reinit_at_start(runner=boom, sleep=lambda s: None, log=logged.append) is False
    assert logged and "FAILED" in logged[0]


def test_the_kiosk_reinitialises_the_panel_before_the_first_frame():
    s = src("scripts/start_ui.sh")
    i = s.index("reinit_at_start")
    assert i < s.index("exec /usr/bin/python3 -u main.py")      # before the app draws
    assert i < s.index("systemctl cat rnsd.service")             # before the rnsd wait
    assert "|| true" in s[i:i + 200]                             # never blocks a start


def test_the_journal_is_kept_across_reboots_on_both_routes():
    from tests.srcutil import func_source
    auto = func_source("workflows/clone.py", "configure_autostart")
    assert "journald-nodemedic.conf" in auto and "50-nodemedic.conf" in auto
    conf = src("scripts/journald-nodemedic.conf")
    assert "Storage=persistent" in conf and "SystemMaxUse=" in conf
    assert "50-nodemedic.conf" in func_source("workflows/medic_setup.py", "_check_autostart")
