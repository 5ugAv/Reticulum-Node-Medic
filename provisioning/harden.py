"""At-rest hardening — tighten sensitive files to owner-only (0600 / 0700).

The SD card IS a credential: it holds the medic's Reticulum/LXMF private identity
keys (the mesh identity that can be impersonated if stolen) plus a location trail
(kin roster + node history). RNS/LXMF write some of these world-readable (0644), so
this runs at every app startup to clamp them back to owner-only. Best-effort and
silent — a missing file or a read-only FS is not an error.
"""

from __future__ import annotations

import os
from typing import List

#: (path, mode) — private keys + location data that must never be world-readable.
_SENSITIVE = [
    ("~/.reticulum-node-medic", 0o700),                 # the whole medic data dir
    ("~/.reticulum-node-medic/kin.json", 0o600),        # names + lat/lon + builders
    ("~/.reticulum-node-medic/registry.json", 0o600),   # location + activity trail
    ("~/.reticulum-node-medic/beacon_targets.json", 0o600),
    ("~/.reticulum-node-medic/tool_identity.json", 0o600),
    ("~/.lxmd/identity", 0o600),                         # LXMF private key
    ("~/.reticulum/storage/transport_identity", 0o600),  # RNS private key
    ("~/.reticulum/storage/identities", 0o700),         # recalled-identity store (dir)
    ("~/.reticulum/storage/known_destinations", 0o600),  # who the medic has heard
]


def harden_permissions(entries=None) -> List[str]:
    """chmod each existing sensitive path to owner-only. Returns the paths it
    changed. Never raises."""
    changed = []
    for path, mode in (entries or _SENSITIVE):
        p = os.path.expanduser(path)
        try:
            if os.path.exists(p) and (os.stat(p).st_mode & 0o777) != mode:
                os.chmod(p, mode)
                changed.append(p)
        except OSError:
            pass
    return changed
