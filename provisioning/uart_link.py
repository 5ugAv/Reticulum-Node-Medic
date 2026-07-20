"""Medic side of the GPIO UART wired link — reach a node over its serial console.

The node side (provisioning.uart_console / sd_edit) bakes a login getty onto the
node's serial console. This is the OTHER end: over a USB-serial adapter wired to
the node's GPIO14/15/GND, drive that getty's login (``login:`` -> user ->
``Password:`` -> shell), then hand back a SerialConnection that runs commands with
the usual sentinel framing. No USB-gadget, no WiFi, no internet — three wires.

``drive_login`` is a pure state machine over ``write``/``read`` callables, so the
login handshake is unit-tested against a scripted fake getty; ``SerialTransport``
and ``connect_uart`` bind it to a real pyserial port (needs the adapter).
"""

from __future__ import annotations

import time
from typing import Callable, List, Optional, Tuple

from transport.connection import SerialConnection

#: Substrings that identify each stage of a Linux serial getty login. Matched
#: case-insensitively. "assword" catches both "Password:" and "password:".
_LOGIN_MARKERS = ("login:",)
_PASSWORD_MARKERS = ("assword",)
_FAIL_MARKERS = ("login incorrect", "incorrect")
#: A unique token we echo to CONFIRM we reached a real shell (a getty won't run
#: it; a shell prints it), rather than guessing at prompt characters ($/#).
_SHELL_TOKEN = "NM_UART_SHELL_OK"


def _read_until(read: Callable[[float], str], markers, timeout: float,
                now: Callable[[], float]) -> Tuple[Optional[str], str]:
    """Accumulate serial input until one of *markers* (case-insensitive) appears
    or *timeout* elapses. Returns ``(matched_marker_or_None, buffer)``."""
    buf = ""
    deadline = now() + timeout
    while now() < deadline:
        buf += read(0.4) or ""
        low = buf.lower()
        for m in markers:
            if m.lower() in low:
                return m, buf
    return None, buf


def drive_login(write: Callable[[str], None], read: Callable[[float], str],
                user: str, password: str, timeout: float = 30.0,
                now: Callable[[], float] = time.monotonic) -> Tuple[bool, str]:
    """Drive a serial getty login using *write* / *read* callables. Returns
    ``(ok, transcript)``. Handles a fresh ``login:`` prompt and an
    already-logged-in console. Fails (False) on bad credentials or if nothing
    answers within *timeout*."""
    transcript = ""
    end = now() + timeout
    write("\r\n")                                     # wake the console

    # Wait for a login prompt. (We do NOT blind-probe with an echo first: a getty
    # sitting at 'login:' would take the echo command as a bogus username.)
    hit, buf = _read_until(read, list(_LOGIN_MARKERS), min(6.0, timeout), now)
    transcript += buf
    if hit is None:
        # No login prompt appeared — maybe a shell is already open. Confirm by
        # echoing a token (safe now: nothing is waiting to eat it as a username).
        return _confirm_shell(write, read, max(3.0, end - now()), now), transcript

    write(user + "\r\n")
    hit, buf = _read_until(read, _PASSWORD_MARKERS, max(2.0, end - now()), now)
    transcript += buf
    if hit is None:
        return False, transcript

    write(password + "\r\n")
    ok = _confirm_shell(write, read, max(4.0, end - now()), now)
    return ok, transcript


def _confirm_shell(write: Callable[[str], None], read: Callable[[float], str],
                   timeout: float, now: Callable[[], float]) -> bool:
    """Echo a unique token and see it come back — proof we're at a shell (a getty
    login/password prompt won't execute the echo)."""
    write(f"echo {_SHELL_TOKEN}\r\n")
    hit, _buf = _read_until(read, [_SHELL_TOKEN] + list(_FAIL_MARKERS), timeout, now)
    return hit == _SHELL_TOKEN


class SerialTransport:
    """pyserial-backed transport for SerialConnection: ``write(str)`` and
    ``read_all(timeout)`` / ``read(timeout)``. Real hardware — needs the adapter."""

    def __init__(self, port: str, baud: int = 115200):
        import serial  # pyserial; imported lazily so tests don't need hardware
        self._ser = serial.Serial(port, baud, timeout=0.2)

    def write(self, data: str) -> None:
        self._ser.write(data.encode("utf-8", "replace"))
        self._ser.flush()

    def read(self, timeout: float) -> str:
        self._ser.timeout = timeout
        return self._ser.read(4096).decode("utf-8", "replace")

    def read_all(self, timeout: float) -> str:
        """Accumulate until a quiet gap or *timeout* — what SerialConnection.run
        expects for a full command response."""
        end = time.monotonic() + timeout
        buf = ""
        while time.monotonic() < end:
            chunk = self.read(0.3)
            if chunk:
                buf += chunk
            elif buf:
                break                                 # quiet after data -> done
        return buf

    def close(self) -> None:
        try:
            self._ser.close()
        except Exception:
            pass


def connect_uart(port: str, user: str, password: str, baud: int = 115200,
                 timeout: float = 30.0) -> SerialConnection:
    """Open the serial port, log in over the getty, and return a ready
    SerialConnection (runs commands via the sentinel framing). Raises on a failed
    login. The node must have a serial console baked in (uart_console / sd_edit)."""
    transport = SerialTransport(port, baud)
    ok, transcript = drive_login(transport.write, transport.read,
                                 user, password, timeout)
    if not ok:
        transport.close()
        raise RuntimeError(
            f"UART login to {port} failed (check wiring TX<->RX, 3.3V, baud "
            f"{baud}, and that the node has a serial console). Last output:\n"
            f"{transcript[-200:]}")
    return SerialConnection(port, baud, transport=transport)
