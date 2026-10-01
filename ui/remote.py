"""Drive the running medic from a shell — open a screen by name, on the real app.

Operator, 2026-09-29, when asked to tap through 38 repainted screens: "I'm sure
you must be able to control the medic and open up different sections of it."
There is no input-injection tool on the medic (no ydotool, no evdev), and faking
touches by pixel would be brittle anyway. This is the honest version: a local
socket INSIDE the app that calls the same ``switch_mode`` a finger does, on the
Kivy main thread, and answers with what happened. Pair it with ``grim`` and a
whole walkthrough is a shell loop with nobody at the panel —
[[verify-through-node-medic]]: the medic's own functions, not a stand-in.

Commands, one line per connection:

    ping                 -> ok
    list                 -> ok <screen names>
    open <screen>        -> ok open <screen>   | err unknown screen ...
    node <hash-prefix>   -> ok node <hash>     | err (unknown / ambiguous)
    home                 -> ok home
    current              -> ok <screen>

SAFETY. AF_UNIX at /tmp/nodemedic-control.sock, mode 0600, owned by the user
the UI runs as: reachable only by someone already logged in as that user, which
on this medic means the operator's SSH key. It cannot flash, wipe, or delete
anything — it only moves between screens. RNM_CONTROL=0 disables it.

The parsing and validation are pure so they are tested without Kivy.
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Iterable, Optional, Tuple

SOCKET_PATH = "/tmp/nodemedic-control.sock"
VERBS = ("ping", "list", "open", "node", "home", "current", "map")
REPLY_TIMEOUT_S = 8.0


def parse_command(line: str) -> Tuple[str, Optional[str]]:
    """``"open vitals"`` -> ``("open", "vitals")``; a bad line -> ``("", None)``."""
    parts = (line or "").strip().split(None, 1)
    if not parts:
        return "", None
    verb = parts[0].lower()
    if verb not in VERBS:
        return "", None
    arg = parts[1].strip() if len(parts) > 1 else None
    if verb in ("open", "node") and not arg:
        return "", None
    if verb not in ("open", "node") and arg:
        return "", None
    return verb, arg


def resolve_node(prefix: str, hashes: Iterable[str]) -> Tuple[Optional[str], str]:
    """The ONE registry hash beginning with *prefix*, or (None, why).

    Same rule as the neighbour matcher: never open the wrong node because the
    prefix was short. Four hex characters is the minimum, so a typo cannot
    match half the registry.
    """
    p = (prefix or "").strip().lower()
    if len(p) < 4 or any(c not in "0123456789abcdef" for c in p):
        return None, "give at least 4 hex characters of the node's hash"
    hits = sorted(h for h in hashes if h and h.lower().startswith(p))
    if not hits:
        return None, f"no node starts with {p}"
    if len(hits) > 1:
        return None, f"{len(hits)} nodes start with {p}: " + ", ".join(h[:8] for h in hits)
    return hits[0], ""


class ControlServer:
    """Serve the commands above for *app* (the running Kivy App)."""

    def __init__(self, app, path: str = SOCKET_PATH):
        self.app = app
        self.path = path
        self._sock = None
        self._thread = None
        self._stop = threading.Event()

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> bool:
        if os.environ.get("RNM_CONTROL", "1") == "0":
            return False
        try:
            if os.path.exists(self.path):
                os.unlink(self.path)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(self.path)
            os.chmod(self.path, 0o600)
            s.listen(2)
            s.settimeout(0.5)
            self._sock = s
        except OSError:
            return False
        self._thread = threading.Thread(target=self._serve, name="rnm-control",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        try:
            if self._sock is not None:
                self._sock.close()
        finally:
            try:
                os.unlink(self.path)
            except OSError:
                pass

    # -- serving ------------------------------------------------------------
    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                conn.settimeout(REPLY_TIMEOUT_S)
                line = conn.recv(512).decode("utf-8", "replace")
                reply = self.handle(line)
                conn.sendall((reply + "\n").encode("utf-8"))
            except Exception as exc:                                   # noqa: BLE001
                try:
                    conn.sendall(f"err {type(exc).__name__}: {exc}\n".encode())
                except OSError:
                    pass
            finally:
                conn.close()

    def handle(self, line: str) -> str:
        """One command -> one reply. Everything that touches the UI runs on the
        Kivy main thread and this waits for it, so the reply describes what the
        screen actually did rather than what was asked."""
        verb, arg = parse_command(line)
        if not verb:
            return "err usage: ping | list | open <screen> | node <hash-prefix> | home | current | map"
        if verb == "ping":
            return "ok"
        return self._on_main(verb, arg)

    def _on_main(self, verb: str, arg: Optional[str]) -> str:
        from kivy.clock import Clock
        done = threading.Event()
        out = {"reply": "err timed out on the UI thread"}

        def run(_dt):
            try:
                out["reply"] = self._apply(verb, arg)
            except Exception as exc:                                   # noqa: BLE001
                out["reply"] = f"err {type(exc).__name__}: {exc}"
            finally:
                done.set()
        Clock.schedule_once(run, 0)
        done.wait(REPLY_TIMEOUT_S)
        return out["reply"]

    def _apply(self, verb: str, arg: Optional[str]) -> str:
        app = self.app
        names = [s.name for s in app.sm.screens]
        if verb == "list":
            return "ok " + " ".join(sorted(names))
        if verb == "current":
            return "ok " + (app.sm.current or "")
        if verb == "map":
            # READ-ONLY: what the map screen is holding right now — the one
            # question a walkthrough could not answer from the saved registry
            # ("Skyfinger isn't on the map", 2026-10-02).
            scr = getattr(app, "scan_screen", None)
            nodes = list(getattr(scr, "_nodes", None) or [])
            rows = ["%s@%s,%s:%s" % (n.get("name"), n.get("lat"), n.get("lon"), n.get("status"))
                    for n in nodes]
            return "ok %d %s" % (len(nodes), " ".join(rows))
        if verb == "home":
            app.switch_mode("home")
            return "ok home"
        if verb == "open":
            if arg not in names:
                return f"err unknown screen {arg!r}; try: list"
            app.switch_mode(arg)
            return f"ok open {arg}"
        if verb == "node":
            reg = app.monitor_service.registry
            h, why = resolve_node(arg, reg.nodes.keys())
            if h is None:
                return "err " + why
            app._open_node_detail({"identity": h})
            return f"ok node {h}"
        return "err unreachable"
