"""Repair a Pi node's radio naming without a rebirth (2026-09-22).

The node "skyfinger" — a Pi Zero 2 W carrying a RAK4631 — was born on the
guide's "I already have a working radio" road before that road asked which
radio it was. Its build wrote the five-vendor udev fallback (no serial to
pin), and its certificate printed the profile DEFAULT board. The node works;
the medic's word about it was wrong, and its /dev/rnode is a wide net.

This module is the correction for a node already in the field: given the
TRUE board key (the operator's word, from the same photo picker birth uses)
and the radio's serial when this medic holds it (the board's own flash
certificate — ui.cert_store.radio_serial_for_board), it produces the exact
udev rule the build would write today, the commands to land it, and the
certificate fields that make the medic's record true again.

Two layers, deliberately:

  * ``plan_radio_repair`` is PURE — no I/O — so the commands can be read,
    tested and, if the medic cannot reach the node, run by hand over SSH.
  * ``repair_radio_name`` runs the plan over a connection object (``run`` ->
    ``(code, stdout, stderr)``, transport.connection's contract) and reports
    ONLY what it read back: the rule file compared byte for byte, the node's
    config pointing at /dev/rnode, and whether /dev/rnode resolves right now.

Same shape as workflows.pi_reporter_push, the other over-SSH update path,
and for the same reason: a fake that mirrors the contract tests the thing
that runs.
"""
from __future__ import annotations

import base64
import shlex
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from workflows.build import (RNODE_SYMLINK, board_text, radio_port_text,
                             rnode_udev_rules)
from workflows.rnode_boards import get_board

RULES_PATH = "/etc/udev/rules.d/60-rnode.rules"


@dataclass
class RadioRepairPlan:
    board_key: str
    board_name: str
    #: install_radio_rule's record shape: {"by", "serial", "source"}.
    rule: Dict[str, str]
    #: The udev rules file, exactly as the build would write it today.
    rules: str
    repaired_on: str = ""
    _cmds: List[str] = field(default_factory=list, repr=False)

    def commands(self, sudo: bool = True) -> List[str]:
        """The three commands that land the rule: write (base64 through a
        pipe, never a heredoc — workflows.build._write_remote_file's scar),
        reload, trigger. *sudo* False for a root login."""
        p = (lambda c: f"sudo -n {c}") if sudo else (lambda c: c)
        b64 = base64.b64encode(self.rules.encode()).decode()
        return [
            f"echo {shlex.quote(b64)} | base64 -d | "
            f"{p(f'tee {shlex.quote(RULES_PATH)}')} >/dev/null",
            p("udevadm control --reload-rules"),
            p("udevadm trigger --subsystem-match=tty"),
        ]

    def cert_fields(self, previous: Optional[Dict] = None) -> Dict:
        """What the medic's certificate for this node should now say —
        the same fields birth writes (workflows.build.birth_certificate),
        plus whose word the board is and WHAT CHANGED. Never ``usb_serial``:
        that key is how save_cert retires OTHER certificates, and the
        radio's own flash record must survive.

        *previous* is the certificate as stored; the correction is recorded
        on it (``repaired_at``, and a ``repairs`` entry with the old and new
        values) so the record keeps its history instead of pretending it
        was always right. Operator, 2026-09-22: "should I delete
        Skyfinger's records in VITALS because they're incorrect?" — no:
        Delete tombstones the node's hashes for seven days and erases the
        certificate. A wrong record is corrected, never recreated.
        """
        fields = {
            "board": self.board_name,
            "board_key": self.board_key,
            "serial_port": radio_port_text(self.rule),
            "radio_rule": dict(self.rule),
            "board_source": ("named by the operator, radio rule repaired "
                             "over SSH" + (f" on {self.repaired_on}"
                                           if self.repaired_on else "")),
        }
        if previous is not None:
            was = {k: previous.get(k) for k in ("board", "serial_port")}
            now = {k: fields[k] for k in ("board", "serial_port")}
            entry = {"at": self.repaired_on, "what": "radio name",
                     "from": was, "to": now}
            fields["repaired_at"] = self.repaired_on
            fields["repairs"] = list(previous.get("repairs") or []) + [entry]
        return fields


def correct_certificate(cert: Dict, plan: RadioRepairPlan,
                        cert_dir: Optional[str] = None) -> bool:
    """Amend the medic's stored certificate for a repaired node IN PLACE.

    Same ``_id``, same file, same born date and notes — only the radio
    fields change and the repair is logged on the record. Goes through
    ui.cert_store.update_fields, which rewrites an existing file and
    nothing else: no delete, no re-save (save_cert's retire-by-serial must
    never run here), no tombstone. False when the medic holds no such file.
    """
    from ui import cert_store
    cid = cert.get("_id") or cert_store.cert_id(cert)
    kw = {"cert_dir": cert_dir} if cert_dir else {}
    return cert_store.update_fields(cid, plan.cert_fields(previous=cert), **kw)


def plan_radio_repair(board_key: str, serial: str = "", source: str = "",
                      repaired_on: str = "") -> RadioRepairPlan:
    """The pure plan. *serial* pins the rule to that radio; "" means the
    vendor net, and the plan says so. An unknown *board_key* is refused —
    a repair that names a board the catalogue does not know would be the
    original bug with a different spelling."""
    board = get_board(board_key or "")
    if board is None:
        raise ValueError(f"unknown RNode board key {board_key!r}")
    serial = (serial or "").strip()
    rule = {"by": "serial" if serial else "vendor", "serial": serial,
            "source": (source or "").strip() if serial else ""}
    return RadioRepairPlan(board_key=board.key, board_name=board_text(board.key),
                           rule=rule, rules=rnode_udev_rules(serial),
                           repaired_on=repaired_on)


def _is_root(conn) -> bool:
    try:
        return (conn.run("id -un")[1] or "").strip() == "root"
    except Exception:                                                  # noqa: BLE001
        return False


def repair_radio_name(conn, board_key: str, serial: str = "", source: str = "",
                      log: Optional[Callable[[str], None]] = None,
                      repaired_on: str = "") -> Tuple[bool, str]:
    """Land the plan on the node and PROVE it. Returns ``(ok, message)``.

    Order matters: the rule is written and read back BEFORE udev is told to
    reload — a reload on a file that did not take would advertise a repair
    that never happened. The node's Reticulum config is then checked for
    ``port = /dev/rnode`` (the build has always written the symlink; a
    hand-edited config would make the rule moot), and /dev/rnode is resolved
    — absent is not a failure, the radio may simply be unplugged, but the
    message says which.
    """
    _log = log or (lambda m: None)
    plan = plan_radio_repair(board_key, serial, source, repaired_on)
    cmds = plan.commands(sudo=not _is_root(conn))
    _log(f"writing {RULES_PATH} ({plan.rule['by']})")
    code, out, err = conn.run(cmds[0])
    if code != 0:
        return False, f"could not write the radio's udev rule: {err or out}"
    back = conn.run(f"cat {RULES_PATH}")[1] or ""
    if back.strip() != plan.rules.strip():
        return False, ("wrote the radio's udev rule but the read back found "
                       "something else — nothing was reloaded")
    for c in cmds[1:]:
        conn.run(c)
    how = (f"serial {plan.rule['serial']}" if plan.rule["by"] == "serial"
           else "by vendor (any tty from the makers this tool flashes — no "
                "serial known)")
    parts = [f"{plan.board_name}: {RNODE_SYMLINK} now named {how}, "
             "checked on the node."]
    cfg = conn.run(f"grep -n 'port = {RNODE_SYMLINK}' ~/.reticulum/config")
    if cfg[0] != 0 or RNODE_SYMLINK not in (cfg[1] or ""):
        return False, (parts[0] + f" But the node's Reticulum config does not "
                       f"point at {RNODE_SYMLINK} — rnsd would still open "
                       "whatever port it names. Not repaired.")
    parts.append(f"Config: port = {RNODE_SYMLINK}.")
    link = conn.run(f"readlink -f {RNODE_SYMLINK}")
    target = (link[1] or "").strip()
    if link[0] == 0 and target and target != RNODE_SYMLINK:
        parts.append(f"{RNODE_SYMLINK} -> {target} right now.")
    else:
        parts.append(f"{RNODE_SYMLINK} does not resolve — no radio attached "
                     "right now; the name will appear when one is plugged in.")
    return True, " ".join(parts)
