"""Crash-safe file writes for the medic's records.

The medic is a battery/solar-powered field device on an SD card, and power can
vanish mid-write — the documented failure mode for these cards is not media
death but a truncated file left by an interrupted write. A plain
``open(path, "w")`` truncates FIRST, so a power cut between truncate and flush
leaves an EMPTY roster / certificate / config: silent data loss discovered
much later.

Every writer here funnels through ``write_bytes``: it writes to a temp file in
the SAME directory, flushes it to the platform (fsync), then atomically renames
it into place — so a reader either sees the old file or the new one, never a
half-written one. It also fsyncs the DIRECTORY, without which the rename itself
can be lost on a power cut. ``os.replace`` is atomic on the target's filesystem
even across a power-cut, which is the whole guarantee we lean on.

  * ``write_json`` — JSON records (kin roster, birth certificates, settings).
  * ``write_text`` — plain-text prefs and configs (~/.reticulum/config, the
    language pref, brightness level, home/backpack markers...).
  * ``write_bytes`` — the atomic core; also used directly for secrets like the
    trust HMAC key, where a short/truncated key means silent fresh-key
    generation and every signature suddenly failing.

TWO-FILE TRANSACTIONS (data + a detached signature sidecar): there is no way to
update two separate files atomically, so a power cut BETWEEN them always leaves
a mismatched pair. Do NOT reach for a ``write_pair`` helper here — the robust
fix is to fold the signature INTO the one file (an ``_integrity`` field) and
write that single file atomically, so there is no ordering to lose. That is
what ``monitor.trust`` now does; the sidecar it still writes is a best-effort
compatibility copy, never the authority. If a caller genuinely cannot fold the
two together, order the writes so the file that readers key on is written LAST,
so a crash leaves the OLD consistent pair intact rather than a torn new one.

Found by the 2026-08-01 bug hunt (kin roster, birth certificates, radio
defaults and the onboard roster were all writing non-atomically) and extended
by the 2026-08-23 field-readiness audit (the trust store's store/sig un-kin
window, the medic's own Reticulum config, and the settings prefs).
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Optional


def write_bytes(path: str, data: bytes, mode: Optional[int] = None) -> bool:
    """Atomically write raw *data* to *path*. Returns True on success.

    Writes a temp file in the same directory, fsyncs it, then ``os.replace``s it
    into place (atomic across a power cut), and fsyncs the directory so the
    rename itself survives. If *mode* is given the final file is chmod'd to it
    (e.g. ``0o600`` for a secret) — otherwise it keeps the temp file's private
    ``mkstemp`` default (0600). Never raises — callers keep their own
    honest-fail reporting."""
    tmp = None
    try:
        path = os.path.expanduser(path)
        parent = os.path.dirname(path) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())          # the bytes reach the card...
        if mode is not None:
            os.chmod(tmp, mode)           # ...with the right permissions...
        os.replace(tmp, path)             # ...then swap in atomically
        tmp = None
        try:                              # ...and persist the rename itself
            dfd = os.open(parent, os.O_DIRECTORY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except (OSError, AttributeError):
            pass                          # not all platforms allow this
        return True
    except Exception:                     # noqa: BLE001
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        return False


def write_text(path: str, text: str, mode: Optional[int] = None) -> bool:
    """Atomically write *text* (UTF-8) to *path*. Returns True on success.

    The plain-text counterpart to ``write_json`` — for config files and single
    scalar prefs where a truncated file would leave the medic misconfigured
    (e.g. a half-written ~/.reticulum/config makes the medic deaf to its own
    mesh). Never raises."""
    return write_bytes(path, text.encode("utf-8"), mode=mode)


def write_json(path: str, data: Any, **dump_kwargs) -> bool:
    """Atomically write *data* as JSON to *path*. Returns True on success.
    Never raises — callers keep their own honest-fail reporting."""
    try:
        payload = json.dumps(data, **dump_kwargs).encode("utf-8")
    except Exception:                     # noqa: BLE001 - unserialisable data
        return False
    return write_bytes(path, payload)
