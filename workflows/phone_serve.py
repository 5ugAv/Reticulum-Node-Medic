"""Serve the carried phone-app APKs to a phone over Wi-Fi.

The medic runs a tiny local HTTP server rooted at the APK cache; the phone (on the
same Wi-Fi / the medic's AP) opens ``http://<medic-ip>:<port>/<app.apk>`` — shown as
a QR on the Comms screen — downloads it and installs (allowing "unknown sources"
once). Receive-only from the mesh's view; it's just a LAN file handout.

The URL building is pure + unit-tested; the server is a thin, in-process wrapper
(it runs on the medic inside the Kivy app, so it needs no injected Connection).
"""

from __future__ import annotations

import os
import socket
import threading
from typing import Optional

DEFAULT_PORT = 8009


def local_ip() -> Optional[str]:
    """The medic's own LAN IP (the address a phone on the same network reaches it
    at), or None if it isn't on a network. No packets are actually sent."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))          # no traffic; just picks the iface
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return None


def build_url(ip: Optional[str], filename: str, port: int = DEFAULT_PORT
              ) -> Optional[str]:
    """The download URL for *filename*, or None when the medic has no IP (so the
    UI can say 'get both on the same Wi-Fi first' instead of a dead QR)."""
    if not ip or not filename:
        return None
    return f"http://{ip}:{port}/{filename}"


class AppServer:
    """Serves the APK cache dir over HTTP on all interfaces. Idempotent start;
    ``stop()`` shuts it down. Daemon thread, so it never blocks app exit."""

    def __init__(self, cache_dir: str, port: int = DEFAULT_PORT):
        self.cache_dir = os.path.expanduser(cache_dir)
        self.port = port
        self._httpd = None

    def start(self) -> Optional[str]:
        """Start serving (if not already) and return the base URL, or None if the
        medic isn't on a network."""
        if self._httpd is None:
            from http.server import SimpleHTTPRequestHandler
            from socketserver import TCPServer
            cache = self.cache_dir

            class _Handler(SimpleHTTPRequestHandler):
                def __init__(self, *a, **k):
                    super().__init__(*a, directory=cache, **k)

                def log_message(self, *a):        # keep the app log quiet
                    pass

            TCPServer.allow_reuse_address = True
            self._httpd = TCPServer(("0.0.0.0", self.port), _Handler)
            threading.Thread(target=self._httpd.serve_forever, daemon=True).start()
        ip = local_ip()
        return f"http://{ip}:{self.port}" if ip else None

    def url_for(self, filename: str) -> Optional[str]:
        return build_url(local_ip(), filename, self.port)

    def is_running(self) -> bool:
        return self._httpd is not None

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


def current_ssid() -> Optional[str]:
    """The Wi-Fi network the medic is ON right now, or None.

    So the phone instruction can NAME the network instead of saying "join the
    medic's Wi-Fi" — which the operator rightly asked about (2026-08-06: "what
    does that mean?"). It is ambiguous because the medic does not create a
    network here: it serves the APK at its OWN address on whatever network it
    happens to be joined to, so what the phone actually needs is to be on THAT
    SAME network. Naming it removes the guesswork.

    ``nmcli`` is what the medic has (there is no ``iw`` on it — established by
    the self-diagnose work). Best-effort: a medic on Ethernet, or with nmcli
    absent, simply gets the generic wording.
    """
    try:
        import subprocess
        out = subprocess.run(
            ["nmcli", "-t", "-f", "IN-USE,SSID", "dev", "wifi"],
            capture_output=True, text=True, timeout=6).stdout or ""
    except Exception:                                             # noqa: BLE001
        return None
    for line in out.splitlines():
        # "*:MyNetwork" — the asterisk marks the connected one.
        if line.startswith("*:"):
            ssid = line[2:].strip()
            return ssid or None
    return None
