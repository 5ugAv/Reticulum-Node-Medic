"""Adopt an already-provisioned node as kin — the non-destructive counterpart to
BIRTH.

BIRTH takes a fresh/foreign board and *builds* it (flash + onboard + verify +
certificate). ADOPTION takes a node that's ALREADY running our firmware on our
canonical settings and simply *enrolls* it: read its identity, confirm it meets
the mesh contract, write a birth certificate and register it in the kin roster so
it shows in VITALS. No flash, no onboarding, no rename — it keeps its working
state exactly as-is.

The steps mirror the tail of the birth workflow (identify -> certificate ->
enroll) and reuse the same ``cert_store``/``kin_roster`` records, so an adopted
node is indistinguishable from a birthed one on VITALS/SCAN.

Readers and record-writers are INJECTED so this unit-tests without a board:
  * ``banner_reader(port) -> str``   — the node's serial boot banner
  * ``status_reader() -> str | None``— the node's HTTP /status body (optional)
  * ``save_cert(cert) -> id``        — real = ui.cert_store.save_cert
  * ``register_kin(**kw)``           — real = monitor.kin_roster.register
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from monitor.node_classifier import classify


@dataclass
class StepResult:
    step: str
    success: bool
    message: str = ""
    skipped: bool = False


#: node board/firmware -> kin roster ``type`` (drives the default link set).
def _node_type(classification: dict) -> str:
    board = (classification.get("board") or "").lower()
    if board.startswith("heltec") or classification.get("kind") == "adopt":
        return "rtnode2400"
    return "rtnode2400"


@dataclass
class AdoptWorkflow:
    board_port: Optional[str] = None
    banner_reader: Optional[Callable[[Optional[str]], str]] = None
    status_reader: Optional[Callable[[], Optional[str]]] = None
    gps_reader: Optional[Callable[[], Optional[Tuple[float, float]]]] = None
    #: operator-confirmed name (overrides the detected one); blank keeps detected.
    node_name_override: str = ""
    #: operator-CONFIRMED (lat, lon); when set it overrides gps_reader (the map
    #: confirm popup already vetted it), so an unseen fix can't be baked in.
    location: Optional[Tuple[float, float]] = None
    save_cert: Optional[Callable[[dict], str]] = None
    register_kin: Optional[Callable[..., object]] = None
    builder_hash: Optional[str] = None

    classification: Optional[dict] = field(default=None, init=False)
    birth_certificate: Optional[dict] = field(default=None, init=False)
    identity_hash: Optional[str] = field(default=None, init=False)
    node_name: str = field(default="", init=False)
    results: List[StepResult] = field(default_factory=list, init=False)

    # -- steps -------------------------------------------------------------
    def _identify(self) -> StepResult:
        banner = ""
        if self.banner_reader is not None:
            try:
                banner = self.banner_reader(self.board_port) or ""
            except Exception as e:      # noqa: BLE001 - report, don't crash the flow
                return StepResult("identify", False, f"Couldn't read the board: {e}")
        status = None
        if self.status_reader is not None:
            try:
                status = self.status_reader()
            except Exception:
                status = None
        c = classify(banner, status)
        self.classification = c
        if c["kind"] != "adopt":
            return StepResult("identify", False, c["reason"])
        self.identity_hash = c["identity_hash"]
        self.node_name = (self.node_name_override.strip()
                          or c.get("node_name") or "adopted-node")
        detail = c.get("node_name") or c.get("board") or "node"
        return StepResult("identify", True,
                          f"Recognised {detail} — {c['reason']}")

    def _certificate(self) -> StepResult:
        c = self.classification or {}
        params = c.get("params") or {}
        location = None
        if self.location is not None:                # operator-confirmed on the map
            location = {"lat": self.location[0], "lon": self.location[1],
                        "source": "confirmed"}
        elif self.gps_reader is not None:
            try:
                fix = self.gps_reader()
            except Exception:
                fix = None
            if fix:
                location = {"lat": fix[0], "lon": fix[1], "source": "gps"}
        self.birth_certificate = {
            "node_name": self.node_name,
            "type": _node_type(c),
            "adopted": True,               # provenance: enrolled, not built here
            "board": c.get("board"),
            "firmware": c.get("firmware"),
            "identity_hash": self.identity_hash,
            "serial_port": self.board_port,
            "frequency_mhz": (params.get("freq", 0) / 1_000_000) or None,
            "bandwidth_khz": (params.get("bw", 0) / 1000) or None,
            "spreading_factor": params.get("sf"),
            "coding_rate": params.get("cr"),
            "txp": params.get("txp"),
            "location": location,
        }
        return StepResult("certificate", True,
                          "Certificate ready for the adopted node.")

    def _enroll(self) -> StepResult:
        cert = self.birth_certificate or {}
        cid = None
        if self.save_cert is not None:
            try:
                cid = self.save_cert(cert)
                cert["_id"] = cid
            except Exception as e:      # noqa: BLE001
                return StepResult("enroll", False, f"Couldn't save certificate: {e}")
        if self.register_kin is not None and self.identity_hash:
            loc = cert.get("location") or {}
            try:
                self.register_kin(
                    self.identity_hash, self.node_name,
                    node_type=cert.get("type", "rtnode2400"),
                    lat=loc.get("lat"), lon=loc.get("lon"),
                    builder=self.builder_hash)
            except Exception as e:      # noqa: BLE001
                return StepResult("enroll", False, f"Couldn't enroll as kin: {e}")
        return StepResult("enroll", True,
                          f"{self.node_name} adopted as kin — now in VITALS.")

    _STEPS = ("_identify", "_certificate", "_enroll")

    def run_all(self, on_progress: Optional[Callable[[StepResult], None]] = None):
        emit = on_progress or (lambda r: None)
        self.results = []
        for name in self._STEPS:
            r = getattr(self, name)()
            self.results.append(r)
            emit(r)
            if not r.success and not r.skipped:
                break
        return self.results

    @property
    def succeeded(self) -> bool:
        return bool(self.results) and all(
            r.success or r.skipped for r in self.results) and \
            len(self.results) == len(self._STEPS)
