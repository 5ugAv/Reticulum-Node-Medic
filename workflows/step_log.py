"""One-line step logging shared by every birth workflow.

Every step of every build must land in the medic's (unbuffered) app log:
that log is how a birth is diagnosed remotely, and how a live lap can be
watched while the operator works. Only RTNodeBuildWorkflow did this — the
RNode flash, V4-RGB and Pi paths ran completely silently, so log monitoring
was blind during those builds (found by the 2026-08-01 bug hunt, after it
had already blinded a live Tracker lap).
"""

from __future__ import annotations


def log_step(result) -> None:
    """Print ``[birth] <name>: ok|FAIL|skip — <message>`` for *result*.
    Never raises — logging must not be able to break a build."""
    try:
        mark = ("skip" if getattr(result, "skipped", False)
                else "ok" if result.success else "FAIL")
        print(f"[birth] {result.name}: {mark} — {result.message}", flush=True)
    except Exception:                      # noqa: BLE001
        pass
