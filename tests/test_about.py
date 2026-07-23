"""About this software (Settings item 9): version, uptime, test-count, graceful
degradation when a shell command fails."""

from provisioning import about


def _run_map(mapping, default=(1, "")):
    """A ShellRunner that returns a canned (code, out) per command substring."""
    def run(cmd):
        for needle, result in mapping.items():
            if needle in cmd:
                return result
        return default
    return run


# -- software version ------------------------------------------------------

def test_software_version_hash_and_branch():
    run = _run_map({
        "rev-parse --short": (0, "a1b2c3d\n"),
        "abbrev-ref": (0, "main\n"),
    })
    assert about.software_version(run) == "a1b2c3d (main)"


def test_software_version_detached_head_drops_branch():
    run = _run_map({
        "rev-parse --short": (0, "a1b2c3d\n"),
        "abbrev-ref": (0, "HEAD\n"),        # detached checkout
    })
    assert about.software_version(run) == "a1b2c3d"


def test_software_version_unknown_when_git_fails():
    run = _run_map({}, default=(128, "fatal: not a git repository"))
    assert about.software_version(run) == "unknown"


# -- repo link -------------------------------------------------------------

def test_repo_link_passthrough_https_strips_dotgit():
    run = _run_map({"remote get-url": (0, "https://github.com/5ugAv/Reticulum-Node-Medic.git\n")})
    assert about.repo_link(run) == "https://github.com/5ugAv/Reticulum-Node-Medic"


def test_repo_link_normalises_ssh_remote():
    run = _run_map({"remote get-url": (0, "git@github.com:5ugAv/Reticulum-Node-Medic.git\n")})
    assert about.repo_link(run) == "https://github.com/5ugAv/Reticulum-Node-Medic"


def test_repo_link_empty_when_no_origin():
    run = _run_map({}, default=(1, "error: No such remote 'origin'"))
    assert about.repo_link(run) == ""


# -- test-suite status (honest, non-blocking) ------------------------------

def test_parse_test_count_from_collect_line():
    assert about.parse_test_count("142 tests collected in 0.34s") == 142
    assert about.parse_test_count("1 test collected in 0.01s") == 1
    # last non-empty line is the one that matters
    assert about.parse_test_count("some noise\n\n88 tests collected in 2s\n") == 88


def test_parse_test_count_none_on_no_collection():
    assert about.parse_test_count("") is None
    assert about.parse_test_count("no tests ran in 0.01s") is None
    assert about.parse_test_count("ERROR: file not found") is None


def test_test_status_reports_count_when_collected():
    run = _run_map({"pytest --collect-only": (0, "142 tests collected in 0.34s\n")})
    assert about.test_status(run) == "142 tests"


def test_test_status_falls_back_to_run_in_ci():
    run = _run_map({"pytest --collect-only": (0, "no tests ran\n")})
    assert about.test_status(run) == "run in CI"
    # never fabricates a passing badge
    assert "passing" not in about.test_status(run).lower()


# -- uptime ----------------------------------------------------------------

def test_uptime_seconds_reads_first_float(tmp_path):
    p = tmp_path / "uptime"
    p.write_text("356623.09 1234567.5\n")
    assert about.uptime_seconds(str(p)) == 356623.09


def test_uptime_seconds_none_when_absent(tmp_path):
    assert about.uptime_seconds(str(tmp_path / "nope")) is None


def test_uptime_seconds_none_when_garbage(tmp_path):
    p = tmp_path / "uptime"
    p.write_text("not a number\n")
    assert about.uptime_seconds(str(p)) is None


def test_format_uptime_days():
    # 3 days, 4 hours, 5 minutes
    secs = (3 * 86400) + (4 * 3600) + (5 * 60)
    assert about.format_uptime(secs) == "Up 3 days, 4 h"


def test_format_uptime_singular_day():
    assert about.format_uptime(86400 + 3600) == "Up 1 day, 1 h"


def test_format_uptime_hours_and_minutes():
    assert about.format_uptime((4 * 3600) + (12 * 60)) == "Up 4 h 12 min"


def test_format_uptime_minutes_only():
    assert about.format_uptime(7 * 60 + 30) == "Up 7 min"


def test_format_uptime_unknown_when_none():
    assert about.format_uptime(None) == "unknown"


# -- full summary degrades gracefully --------------------------------------

def test_summary_all_commands_fail_is_graceful(tmp_path):
    run = _run_map({}, default=(1, "boom"))
    s = about.summary(run, uptime_path=str(tmp_path / "nope"))
    assert s["version"] == "unknown"
    assert s["test_status"] == "run in CI"
    assert s["uptime"] == "unknown"
    assert s["license"] == "MIT"
    assert s["repo"] == ""
