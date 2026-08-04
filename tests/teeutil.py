"""Decode the privileged base64 writes that ``provisioning.*`` build.

``provisioning/{sd_edit,gadget,uart_console}._tee`` no longer emit a heredoc.
A quoted heredoc marker stops variable expansion but NOT early termination, so
a line in the *content* equal to the marker ended the heredoc and turned the
remainder into shell — and in ``sd_edit`` that content comes off an
operator-supplied SD card. The content is now base64'd, whose alphabet holds no
shell metacharacters.

Tests still need to assert WHAT gets written. Doing that by substring-matching
the command only worked while the content sat in the command literally; it
silently stopped meaning anything the moment the encoding changed. Decode
instead, so the assertions survive the next encoding change too.
"""
import base64
import re

#: ``echo <b64> | base64 -d | sudo -n tee <path> > /dev/null``
_TEE = re.compile(
    r"echo\s+([A-Za-z0-9+/=]+)\s*\|\s*base64\s+-d\s*\|\s*sudo\s+-n\s+tee\s+(\S+)")


def decode_tee(cmd):
    """``(path, content)`` for a base64 tee command, or None if *cmd* isn't one."""
    m = _TEE.search(cmd or "")
    if not m:
        return None
    return (m.group(2), base64.b64decode(m.group(1)).decode())


def tee_writes(cmds):
    """Every ``(path, content)`` pair among *cmds*."""
    return [d for d in (decode_tee(c) for c in cmds) if d]


def wrote(cmds, path_part, content_part):
    """True if some command writes content containing *content_part* to a path
    containing *path_part*."""
    return any(path_part in p and content_part in c
               for p, c in tee_writes(cmds))


def mentions(cmds, text):
    """True if *text* appears in any command — either literally, or inside the
    decoded content of a base64 tee.

    Use this for NEGATIVE assertions (``assert not mentions(...)``). A plain
    ``not any(text in c for c in cmds)`` became vacuously true the moment the
    content was base64'd: it would keep passing while the thing it forbids was
    written on every run. Checking both forms keeps such assertions fail-closed.
    """
    if any(text in (c or "") for c in cmds):
        return True
    return any(text in content for _path, content in tee_writes(cmds))
