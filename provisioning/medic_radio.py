"""Retune the medic's OWN radio to the tool-wide radio defaults.

When the operator changes the default radio parameters (Settings ▸ Default
radio parameters), every node they birth uses the new settings — but the medic
itself would still be listening on the old ones and go deaf to its own fleet.
This module rewrites the five LoRa keys inside the RNodeInterface section of
``~/.reticulum/config`` (Jonesey's interface) and restarts rnsd (passwordless
sudo — verified in the medic's sudoers) so the medic can transmit and receive
on whatever the operator chose.

``render_config`` is a pure text transform (unit-tested); only sections whose
body says ``type = RNodeInterface`` are touched, and only the five LoRa keys —
ports, callsigns and every other interface stay byte-identical.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Dict, Optional, Tuple

CONFIG_PATH = os.path.expanduser("~/.reticulum/config")

#: config key -> (params key, to-config-value). RNS wants Hz integers.
_KEYS = {
    "frequency": ("freq", lambda p: str(int(round(float(p) * 1_000_000)))),
    "bandwidth": ("bw", lambda p: str(int(round(float(p) * 1000)))),
    "txpower": ("txp", lambda p: str(int(p))),
    "spreadingfactor": ("sf", lambda p: str(int(p))),
    "codingrate": ("cr", lambda p: str(int(p))),
}

_HEADER_RE = re.compile(r"^\s*\[+[^\]]+\]+\s*$")
_TYPE_RE = re.compile(r"^\s*type\s*=\s*RNodeInterface\s*$")


def render_config(text: str, params: Dict) -> str:
    """Return *text* with the five LoRa keys rewritten (from *params*, the
    radio_defaults 5-key dict) inside every RNodeInterface section. Pure —
    anything else in the file is preserved byte-for-byte."""
    lines = text.splitlines(keepends=True)
    # section spans: from each [..]/[[..]] header to the next header
    spans = []
    start = None
    for i, ln in enumerate(lines):
        if _HEADER_RE.match(ln):
            if start is not None:
                spans.append((start, i))
            start = i
    if start is not None:
        spans.append((start, len(lines)))
    for a, b in spans:
        if not any(_TYPE_RE.match(lines[j]) for j in range(a, b)):
            continue                          # not an RNode interface — skip
        for j in range(a, b):
            for cfg_key, (pk, conv) in _KEYS.items():
                m = re.match(rf"^(\s*{cfg_key}\s*=\s*)\S.*$", lines[j])
                if m:
                    eol = "\n" if lines[j].endswith("\n") else ""
                    lines[j] = m.group(1) + conv(params[pk]) + eol
    return "".join(lines)


def retune_medic(params: Optional[Dict] = None,
                 config_path: str = CONFIG_PATH,
                 restart: bool = True) -> Tuple[bool, str]:
    """Point the medic's own RNode at *params* (default: the saved tool-wide
    defaults) and restart rnsd. No-op (and no rnsd restart) when the config
    already matches. Returns ``(ok, human message)``."""
    if params is None:
        from provisioning.radio_defaults import load_defaults
        params = load_defaults()
    try:
        with open(config_path) as f:
            text = f.read()
    except OSError as e:
        return False, f"Couldn't read the medic's Reticulum config: {e}"
    new = render_config(text, params)
    if new == text:
        return True, "Medic radio already matches."
    try:
        shutil.copy2(config_path, config_path + ".bak-retune")
        with open(config_path, "w") as f:
            f.write(new)
    except OSError as e:
        return False, f"Couldn't write the medic's Reticulum config: {e}"
    if restart:
        try:
            r = subprocess.run(["sudo", "-n", "systemctl", "restart", "rnsd"],
                               capture_output=True, text=True, timeout=60)
        except Exception as e:      # noqa: BLE001
            return False, f"Config written but rnsd restart failed: {e}"
        if r.returncode != 0:
            return False, ("Config written but rnsd restart failed: "
                           + (r.stderr or r.stdout or "").strip()[-120:])
    return True, "Medic radio retuned to match (rnsd restarted)."
