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
