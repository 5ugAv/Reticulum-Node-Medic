"""The FAULT-INJECTION MATRIX — chaos-sweep every workflow (operator order,
2026-08-01: 'a bug hunting command to chew through all the processes').

For every buildable workflow, run it once per fault position N: the first N
commands succeed, then EVERY command (and file push) fails. Each run asserts
the honest-fail laws the tool lives by:

  1. run_all NEVER raises — a failing command must surface as a failed
     StepResult, not a crash (the 2026-07-30 04:45 stranded-flash-lock class).
  2. Every result is a well-formed StepResult (str name, bool success).
  3. A failed, non-skipped step carries a non-empty message NAMING the
     failure — silent failure is a lie.
  4. Once a non-skipped step has FAILED, no later step may report success —
     a green after a red is a false-success path (workflows either stop on
     failure or explicitly skip the rest).

~150 emulated runs, no hardware, sub-second. When a new workflow is born,
add its constructor to WORKFLOWS and it inherits the whole matrix.
"""

from transport.connection import EmulatedConnection
from node_profile import NodeProfile


class FaultAfter(EmulatedConnection):
    """First *n* commands behave (default ok); everything after fails —
    commands return (1, "", "injected fault") and pushes return False."""

    def __init__(self, n: int):
        super().__init__(default_code=0, default_stdout="ok")
        self.n = n
        self.calls = 0

    def _spent(self) -> bool:
        self.calls += 1
        return self.calls > self.n

    def run(self, command, timeout: int = 30):
        if self._spent():
            self.history.append(command)
            return (1, "", "injected fault")
        return super().run(command, timeout)

    def run_interactive(self, command, interactions, timeout: int = 400):
        if self._spent():
            self.history.append(command)
            return (1, "", "injected fault")
        self.history.append(command)
        return (0, "ok", "")

    def push_file(self, local_path, remote_path):
        if self._spent():
            return False
        return super().push_file(local_path, remote_path)


def _rnode_flash(conn):
    from workflows.rnode_flash import RNodeFlashWorkflow
    from workflows.rnode_boards import get_board
    return RNodeFlashWorkflow(conn, get_board("heltec32_v4"),
                              port="/dev/ttyACM9",
                              work_ports_fn=lambda: ["/dev/ttyACM9"])


def _tracker_flash(conn):
    from workflows.rnode_flash import RNodeFlashWorkflow
    from workflows.rnode_boards import get_board
    return RNodeFlashWorkflow(conn, get_board("heltec_wireless_tracker"),
                              port="/dev/ttyACM9",
                              work_ports_fn=lambda: ["/dev/ttyACM9"])


def _v4_rgb(conn):
    from workflows.rnode_v4_rgb import HeltecV4RGBWorkflow
    return HeltecV4RGBWorkflow(conn, port="/dev/ttyACM9")


def _rtnode(conn):
    from workflows.rtnode_build import RTNodeBuildWorkflow
    return RTNodeBuildWorkflow(conn, NodeProfile(), gps_reader=lambda: None)


def _pi_build(conn):
    from workflows.build import BuildWorkflow
    return BuildWorkflow(conn, NodeProfile())


WORKFLOWS = {
    "rnode_flash_v4": _rnode_flash,
    "rnode_flash_tracker": _tracker_flash,
    "v4_rgb": _v4_rgb,
    "rtnode_build": _rtnode,
    "pi_build": _pi_build,
}

FAULT_POSITIONS = range(0, 30)


def _assert_honest(results, wf_name, n):
    ctx = f"[{wf_name} @ fault N={n}]"
    assert isinstance(results, list) and results, f"{ctx} no results returned"
    failed_seen = False
    for r in results:
        assert isinstance(r.name, str) and r.name, f"{ctx} nameless step"
        assert isinstance(r.success, bool), f"{ctx} non-bool success in {r.name}"
        skipped = bool(getattr(r, "skipped", False))
        if failed_seen and not skipped:
            assert not r.success, (
                f"{ctx} FALSE SUCCESS: step '{r.name}' reported ok AFTER an "
                f"earlier step failed — green after red is a lie")
        if not r.success and not skipped:
            failed_seen = True
            assert (getattr(r, "message", "") or "").strip(), (
                f"{ctx} SILENT FAILURE: step '{r.name}' failed with no message")


def test_fault_matrix_every_workflow_every_position():
    problems = []
    for wf_name, factory in WORKFLOWS.items():
        for n in FAULT_POSITIONS:
            conn = FaultAfter(n)
            try:
                results = factory(conn).run_all()
            except Exception as e:      # noqa: BLE001 — law 1
                problems.append(f"[{wf_name} @ N={n}] run_all RAISED "
                                f"{type(e).__name__}: {e}")
                continue
            try:
                _assert_honest(results, wf_name, n)
            except AssertionError as e:
                problems.append(str(e))
    assert not problems, (
        f"{len(problems)} honest-fail violations:\n" + "\n".join(problems[:12]))


def test_total_blackout_still_reports_a_named_failure():
    """N=0: the very first command fails. Every workflow must still return a
    result list whose first non-skipped failure names the problem."""
    for wf_name, factory in WORKFLOWS.items():
        results = factory(FaultAfter(0)).run_all()
        bad = [r for r in results if not r.success and not getattr(r, "skipped", False)]
        assert bad, f"[{wf_name}] total blackout produced no failed step at all"
