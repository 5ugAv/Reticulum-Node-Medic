"""Touchscreen vault-unlock — STUB for the encrypt-at-rest boot integration.

Design (see docs/encrypt-at-rest.md): at boot, ``nodemedic-vault.service`` calls
``systemd-ask-password`` and BLOCKS; rnsd + lxmd wait behind it. This module is
the other half — the touchscreen agent that answers that request with the
passphrase the operator types, so the vault mounts and the daemons proceed.

Two pieces:

  * ``answer_pending_ask_password(passphrase)`` — the systemd password-agent
    protocol: find the newest request in /run/systemd/ask-password/, read its
    reply socket, send "+<passphrase>". Pure-ish (filesystem + socket only),
    no Kivy. This is the load-bearing glue; full wiring is the follow-up.
  * ``VaultUnlockScreen`` — a minimal Kivy screen that shows a passphrase field
    (reusing the app's OnScreenKeyboard) and, on submit, calls the answerer.
    Imported lazily so headless/test environments never require Kivy.

NOTE ON PRIVILEGE: /run/systemd/ask-password is root-only. In production run
this agent from a tiny root helper (or grant the app a scoped sudoers entry for
the mount helper) — that decision is flagged for human sign-off in the doc. The
answerer is written so it can be unit-tested against a fake ask directory.
"""

from __future__ import annotations

import glob
import os
import socket
from typing import Optional

ASK_DIR = "/run/systemd/ask-password"


def _parse_ask_file(path: str) -> Optional[str]:
    """Return the reply socket path from a systemd 'ask.XXXX' file, or None."""
    try:
        with open(path, "r") as fh:
            for line in fh:
                if line.startswith("Socket="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        return None
    return None


def find_pending_socket(ask_dir: str = ASK_DIR) -> Optional[str]:
    """Newest pending ask-password reply socket, or None if nothing is asking."""
    files = sorted(glob.glob(os.path.join(ask_dir, "ask.*")),
                   key=lambda p: os.path.getmtime(p) if os.path.exists(p) else 0)
    for f in reversed(files):
        sock = _parse_ask_file(f)
        if sock and os.path.exists(sock):
            return sock
    return None


def answer_pending_ask_password(passphrase: str, ask_dir: str = ASK_DIR) -> bool:
    """Send *passphrase* to the pending systemd ask-password request.

    Protocol: connect to the reply AF_UNIX datagram socket and send a single
    packet '+' + passphrase (a leading '-' would signal cancellation). Returns
    True if a request was found and answered."""
    sock_path = find_pending_socket(ask_dir)
    if not sock_path:
        return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        s.connect(sock_path)
        s.sendall(b"+" + passphrase.encode("utf-8"))
        return True
    except OSError:
        return False
    finally:
        s.close()


# --------------------------------------------------------------------------- #
# Kivy screen stub. Lazily importable; only pulls in Kivy when instantiated.
# --------------------------------------------------------------------------- #

def build_unlock_screen():  # pragma: no cover (UI stub, needs Kivy + display)
    """Return a VaultUnlockScreen instance. Call from ui.app when the vault is
    configured but not yet mounted at startup."""
    from kivy.uix.screenmanager import Screen
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.button import Button
    from kivy.uix.label import Label
    from kivy.uix.textinput import TextInput

    class VaultUnlockScreen(Screen):
        def __init__(self, on_unlocked=None, **kw):
            super().__init__(**kw)
            self.on_unlocked = on_unlocked
            root = BoxLayout(orientation="vertical", padding=24, spacing=16)
            root.add_widget(Label(text="Unlock Node Medic vault"))
            self.field = TextInput(password=True, multiline=False,
                                   hint_text="passphrase", size_hint_y=None,
                                   height=64)
            root.add_widget(self.field)
            self.status = Label(text="", size_hint_y=None, height=32)
            root.add_widget(self.status)
            btn = Button(text="Unlock", size_hint_y=None, height=64)
            btn.bind(on_release=lambda *_: self._submit())
            root.add_widget(btn)
            self.add_widget(root)
            # Real build wires OnScreenKeyboard (ui.onscreen_keyboard) to self.field.

        def _submit(self):
            passphrase = self.field.text
            self.field.text = ""
            if answer_pending_ask_password(passphrase):
                self.status.text = "Unlocking…"
                if self.on_unlocked:
                    self.on_unlocked()
            else:
                # No boot request pending (e.g. app-driven unlock): fall back to
                # invoking the mount helper directly via a scoped sudo rule.
                self.status.text = "No unlock request pending."

    return VaultUnlockScreen()
