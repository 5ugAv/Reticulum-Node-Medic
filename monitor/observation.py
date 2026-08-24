"""One value type a fact cannot be stored without.

Every honesty bug the VITALS screen has ever grown is the same shape: a value
was observed ONCE, stored in a bare field, then read back later by code with no
way left to ask "when was this true, and how did we learn it?" — so a stale
reading rendered as current fact. The registry hand-patched that shape five
times, one parallel timestamp field per incident (last_seen, last_direct,
mesh_heard, last_echo_at, last_heard_announce_at), each with its own gating
scattered across the codebase.

``Observation`` is the antidote: a small, pure, immutable value type that binds
a value to WHEN it was observed and HOW (its source). A fact cannot be put into
one without stamping its time, and the age/never/impossible policy that used to
be copy-pasted at every read site now lives in exactly one place — ``age_at``.

THE THREE STATES, and the hard rule that separates them:

  * NEVER OBSERVED  — the WHOLE Observation is ``None``. This NEVER means
    "couldn't observe right now"; it means "no observation has ever existed".
    A guess or a fallback that could not be measured is a real Observation with
    ``source="assumed"`` carrying that honest label — never a None standing in
    for ignorance. (This is the distinction that kept a never-heard fleet row
    from rendering "SEEN 0.0h" green: no observation != an observation of zero.)

  * IMPOSSIBLE  — an observation whose ``observed_at`` sits in the FUTURE by
    more than benign clock jitter: the reading predates the current clock (a
    backward step). Its age is unknowable, not zero, so ``age_at`` flags it and
    the renderer shows "SEEN ?" grey, never a clamped "0.0h" green.

  * MEASURED  — a real, non-negative age (a small backward jitter clamps to 0.0).

Pure and deterministic: ``now`` is always passed in, never read from the clock,
so this stays unit-testable and the whole tree of derived surfaces agrees by
construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, Optional, TypeVar

T = TypeVar("T")

#: A backward clock step THIS small is benign jitter, not "the reading predates
#: the clock". Without a deadband ANY raw age < 0 — even a three-second step
#: behind a node heard two seconds ago — would flip a LIVE node to grey
#: "SEEN ?", a dishonesty the other way (crying unknown on a healthy node).
#: Only a step LARGER than this counts as a genuine clock-step worth flagging;
#: inside the band the age clamps to 0.0 and the reading is treated as fresh.
#:
#: This is THE ONE place the deadband lives now. It used to be duplicated in the
#: registry (to_dashboard AND _status_base each re-derived the same threshold);
#: routing both through ``age_at`` collapsed those copies into this constant.
CLOCK_SKEW_TOLERANCE_S = 120.0        # two minutes

#: The honest vocabulary for how a value was learned. Kept as documentation and
#: for callers that want to check; deliberately NOT enforced by raising — a
#: corrupt or forward-compatible source string must load, not crash the app.
#:  * "beacon"     — a decoded health beacon the node itself emitted
#:  * "http"       — a reachable HTTP /status poll on the LAN
#:  * "announce"   — a genuine (non-replayed) RNS announce
#:  * "echo"       — a byte-identical REPLAY the transport re-emitted from its
#:                   cache (rnsd answering a path request with a stored
#:                   announce): the mesh repeating the node's last words, NEVER
#:                   the node speaking. Recorded for diagnosis; never a sighting.
#:  * "path-table" — a route learned from the mesh (a ROUTE, not the node
#:                   speaking — weaker evidence; see registry.ingest_mesh)
#:  * "direct"     — the node's own direct word, kind not separately retained
#:                   (a beacon/http/announce that out-dated a route fold)
#:  * "operator"   — a human asserted it
#:  * "assumed"    — a fallback/guess wearing its honest label (NOT a sighting)
#:  * "legacy"     — reconstructed from a pre-Observation registry file
#:  * "unknown"    — a bare timestamp written with no source to give (the
#:                   compat ``NodeRecord.last_seen`` setter, a corrupt source
#:                   string): the WHEN is real, the HOW was never supplied
KNOWN_SOURCES = frozenset({
    "beacon", "http", "announce", "echo", "path-table", "direct",
    "operator", "assumed", "legacy", "unknown",
})


@dataclass(frozen=True)
class Age:
    """The result of asking an Observation "how long ago?".

    A bare float could not carry the one thing the SEEN line most needs to be
    honest about — that a negative reading is *impossible*, not fresh — so the
    answer is a tiny record instead: a clamped, never-negative ``seconds`` plus
    the ``impossible`` flag the renderer paints grey off. (The "flag on the
    return" the design called for.) NEVER-observed is not an Age at all; it is
    the absence of an Observation, handled by ``Observation.age_of`` returning
    ``None``.
    """
    seconds: float          #: clamped >= 0 — "how long ago", benign skew -> 0.0
    impossible: bool        #: observed_at predates ``now`` beyond the skew band

    @property
    def hours(self) -> float:
        """Age in hours — the unit the VITALS staleness thresholds work in."""
        return self.seconds / 3600.0


@dataclass(frozen=True)
class Observation(Generic[T]):
    """An immutable ``(value, observed_at, source)`` triple — a fact bound to
    the moment and the manner it was learned. Frozen so a stored observation
    cannot be silently mutated out from under the surfaces that render it; to
    move it in time (a GPS clock step) call ``rebased``, which returns a copy.
    """
    value: T
    observed_at: float      #: epoch seconds WHEN the value was observed
    source: str             #: HOW it was learned — see ``KNOWN_SOURCES``

    # -- construction ------------------------------------------------------

    @classmethod
    def at(cls, observed_at: float, source: str,
           value: T = None) -> "Observation[T]":
        """A sighting stamped at ``observed_at``. For pure LIVENESS (was the
        node heard?) the *value* is immaterial — only the WHEN and the HOW
        matter — so it defaults to ``None`` and callers pass just the time and
        source. ``Observation.at(now, "beacon")`` reads as what it is."""
        return cls(value=value, observed_at=float(observed_at), source=source)

    # -- the one age policy ------------------------------------------------

    def age_at(self, now: float,
               skew_tolerance_s: float = CLOCK_SKEW_TOLERANCE_S) -> Age:
        """How long ago this was observed, as of ``now`` — the ONE place the
        clock-skew policy lives. A non-negative age is returned as-is. A
        negative age (``now`` before ``observed_at``) is a clock stepping
        backward: within ``skew_tolerance_s`` it is benign jitter and clamps to
        0.0 (still fresh); beyond it the reading PREDATES the clock and is
        flagged ``impossible`` (its true age is unknowable, so the renderer must
        show "?", never a clamped zero painted green)."""
        raw = now - self.observed_at
        if raw < 0.0:
            return Age(seconds=0.0, impossible=raw < -skew_tolerance_s)
        return Age(seconds=raw, impossible=False)

    @classmethod
    def age_of(cls, obs: "Optional[Observation[T]]", now: float,
               skew_tolerance_s: float = CLOCK_SKEW_TOLERANCE_S) -> Optional[Age]:
        """``age_at`` for a possibly-``None`` observation — the None-safe front
        door callers reach for. ``None`` in means NEVER observed, and never
        observed has no age: it returns ``None``, distinct from the ``Age`` of a
        real observation (whose ``seconds`` may be 0.0 but is a measurement).
        This is the boundary that keeps "no fact" from collapsing into "a fact
        of zero"."""
        if obs is None:
            return None
        return obs.age_at(now, skew_tolerance_s)

    def is_fresh(self, now: float, within_s: float,
                 skew_tolerance_s: float = CLOCK_SKEW_TOLERANCE_S) -> bool:
        """True only if this is a real, possible measurement no older than
        ``within_s``. An impossible (clock-stepped) reading is NOT fresh — its
        age is unknowable, and unknowable must never pass for recent."""
        age = self.age_at(now, skew_tolerance_s)
        return (not age.impossible) and age.seconds <= within_s

    # -- moving in time (GPS clock discipline) -----------------------------

    def rebased(self, delta: float) -> "Observation[T]":
        """A copy shifted ``delta`` seconds forward in the wall clock, keeping
        its value and source. When the system clock is stepped (GPS time
        discipline) the stamp must move with it, or a node heard five minutes
        ago reads hours silent after a forward step. Frozen type, so this
        returns a new Observation rather than mutating in place."""
        return Observation(value=self.value,
                           observed_at=self.observed_at + delta,
                           source=self.source)

    # -- serialization -----------------------------------------------------

    def to_dict(self) -> dict:
        """Persist as ``{value, observed_at, source}``. A ``None`` observation
        (never observed) persists as JSON ``null`` — see ``from_dict`` — so the
        never/measured distinction survives a restart intact."""
        return {"value": self.value,
                "observed_at": self.observed_at,
                "source": self.source}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Optional[Observation[T]]":
        """Rebuild from ``to_dict`` output. ``None`` (JSON null) round-trips to
        the never-observed state — NOT to an observation of ``None`` — because
        the whole-thing-is-None rule is the never rule.

        A CORRUPT entry must load, not crash (the vocabulary promise, and the
        registry loads a whole fleet from one file — one bad node must never
        take the rest down). Anything that is not a dict with a finite numeric
        ``observed_at`` degrades to never-observed (``None``): a bare string, a
        number, ``{}``, or ``{"observed_at": "notafloat"}`` all return ``None``
        rather than raising here or deferring the crash to ``age_at`` at render
        time. A non-canonical ``source`` string is kept as-is (forward-compat);
        a missing one defaults to ``"legacy"``."""
        if not isinstance(data, dict):
            return None
        try:
            observed_at = float(data.get("observed_at"))
        except (TypeError, ValueError):
            return None
        if observed_at != observed_at or observed_at in (
                float("inf"), float("-inf")):        # NaN / +-inf are not a time
            return None
        source = data.get("source", "legacy")
        if not isinstance(source, str):
            source = "unknown"
        return cls(value=data.get("value"),
                   observed_at=observed_at, source=source)

    @classmethod
    def from_legacy(cls, observed_at: Optional[float],
                    source: str = "legacy") -> "Optional[Observation[None]]":
        """Adapt a pre-Observation bare timestamp (an old registry file's raw
        ``last_seen`` float) into an Observation. ``None`` stays never-observed;
        a real epoch becomes a liveness observation carrying the honest
        ``source="legacy"`` label — we know WHEN the old code last heard it, but
        never HOW, so we do not pretend to."""
        if observed_at is None:
            return None
        try:
            observed_at = float(observed_at)
        except (TypeError, ValueError):
            return None
        if observed_at != observed_at or observed_at in (
                float("inf"), float("-inf")):        # NaN / +-inf are not a time
            return None
        if not isinstance(source, str):
            source = "unknown"
        return cls(value=None, observed_at=observed_at, source=source)
