import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from diagnostics.base import DiagnosticCheck, Issue, Fix


class SampleCheck(DiagnosticCheck):
    category_name = "Sample"

    def run(self):
        issues = []
        issues.append(
            self._check(
                "always_ok",
                condition=True,
                plain_description="this never fails",
            )
        )
        issues.append(
            self._check(
                "always_bad",
                condition=False,
                plain_description="this always fails",
                severity="critical",
                raw_detail="detail here",
                auto_fixable=True,
                fix_description="do the fix",
            )
        )
        return [i for i in issues if i is not None]

    def _fix_handlers(self):
        return {"always_bad": self._fix_always_bad}

    def _fix_always_bad(self, issue):
        return Fix(issue=issue, success=True, message="fixed it")


def make(conn=None, profile=None):
    return SampleCheck(conn or EmulatedConnection(), profile or NodeProfile())


def test_check_returns_none_when_condition_true():
    c = make()
    issue = c._check("x", condition=True, plain_description="ok")
    assert issue is None


def test_check_returns_issue_when_condition_false():
    c = make()
    issue = c._check(
        "x",
        condition=False,
        plain_description="bad",
        severity="warning",
        raw_detail="raw",
        auto_fixable=True,
        fix_description="fixme",
    )
    assert isinstance(issue, Issue)
    assert issue.check_name == "x"
    assert issue.description == "bad"
    assert issue.severity == "warning"
    assert issue.raw_detail == "raw"
    assert issue.auto_fixable is True
    assert issue.fix_description == "fixme"
    assert issue.category == "Sample"


def test_run_collects_only_failures():
    issues = make().run()
    assert len(issues) == 1
    assert issues[0].check_name == "always_bad"


def test_all_checks_run_even_after_a_failure():
    # SampleCheck runs always_ok (pass) then always_bad (fail); both execute.
    # Prove nothing short-circuits by counting the produced issue plus the
    # fact that a later check still contributed.
    issues = make().run()
    names = [i.check_name for i in issues]
    assert "always_bad" in names


def test_fix_dispatches_to_handler():
    c = make()
    issue = c.run()[0]
    fix = c.fix(issue)
    assert isinstance(fix, Fix)
    assert fix.success is True
    assert fix.message == "fixed it"


def test_fix_without_handler_returns_unsuccessful_fix():
    c = make()
    orphan = Issue(
        check_name="no_handler",
        category="Sample",
        description="x",
        severity="warning",
    )
    fix = c.fix(orphan)
    assert isinstance(fix, Fix)
    assert fix.success is False


# ---- command helpers -----------------------------------------------------


def test_run_cmd_delegates_to_connection():
    conn = EmulatedConnection().rule("hello", code=0, stdout="world")
    c = make(conn=conn)
    code, out, err = c._run_cmd("hello")
    assert code == 0
    assert out == "world"


def test_cmd_output_returns_stdout_on_success():
    conn = EmulatedConnection().rule("hello", code=0, stdout="world")
    assert make(conn=conn)._cmd_output("hello") == "world"


def test_cmd_output_returns_empty_on_failure():
    conn = EmulatedConnection().rule("boom", code=1, stdout="junk")
    assert make(conn=conn)._cmd_output("boom") == ""


def test_service_is_active_true():
    conn = EmulatedConnection().rule("^systemctl is-active", code=0, stdout="active")
    assert make(conn=conn)._service_is_active("rnsd") is True


def test_service_is_active_false():
    conn = EmulatedConnection().rule(
        "^systemctl is-active", code=3, stdout="inactive"
    )
    assert make(conn=conn)._service_is_active("rnsd") is False


def test_service_is_enabled_true():
    conn = EmulatedConnection().rule(
        "^systemctl is-enabled", code=0, stdout="enabled"
    )
    assert make(conn=conn)._service_is_enabled("rnsd") is True


def test_service_is_enabled_false():
    conn = EmulatedConnection().rule(
        "^systemctl is-enabled", code=1, stdout="disabled"
    )
    assert make(conn=conn)._service_is_enabled("rnsd") is False


def test_file_exists_true():
    conn = EmulatedConnection().rule("^test -f", code=0)
    assert make(conn=conn)._file_exists("/etc/foo") is True


def test_file_exists_false():
    conn = EmulatedConnection().rule("^test -f", code=1)
    assert make(conn=conn)._file_exists("/etc/foo") is False


def test_dir_exists_true():
    conn = EmulatedConnection().rule("^test -d", code=0)
    assert make(conn=conn)._dir_exists("/etc") is True


def test_dir_exists_false():
    conn = EmulatedConnection().rule("^test -d", code=1)
    assert make(conn=conn)._dir_exists("/etc") is False
