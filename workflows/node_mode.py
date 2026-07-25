"""Home / Backpack mode — the medic's network role.

A MOBILE transport node harms the mesh: other nodes route through it and it
announces paths, so as it moves those paths break and announces churn — wasted
airtime and unstable routing. So the medic runs in one of two modes:

* **home**     — a full propagation node: ``enable_transport = Yes`` (routes/relays
  for the mesh) AND the LXMF propagation node on (``enable_node = yes`` — holds
  messages for offline users). The medic is stable infrastructure.
* **backpack** — a mobile leaf: both OFF. It still HEARS announces and runs its
  monitoring + originates its own traffic, but it doesn't route or store-and-forward
  for anyone, so moving it can't disturb the network. Keep it here whenever the
  medic is roving — or permanently, if a dedicated propagation node (e.g. EVERYWHERE)
  covers home.

Flipping mode rewrites the two config lines and restarts the rnsd + lxmd services.
All access is via an injected ``Connection``, so it's unit-testable without touching
a real config or a live daemon.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from transport.connection import Connection

RNS_CONFIG = "~/.reticulum/config"
LXMD_CONFIG = "~/.lxmd/config"
#: Persisted preference so the UI shows the mode + it survives an app restart.
MODE_FILE = "~/.reticulum-node-medic/node_mode"
#: What HOME mode means — set in Settings ▸ Home mode. A local UI pref (direct
#: file IO, like alerts/retention), separate from the mode marker above.
HOME_PROFILE_FILE = os.path.expanduser("~/.reticulum-node-medic/home_profile")

HOME, BACKPACK = "home", "backpack"
PROPAGATION, TRANSPORT = "propagation", "transport"


def load_home_profile() -> str:
    """The chosen HOME role: 'propagation' (routing + store-and-forward, default)
    or 'transport' (routing only)."""
    try:
        with open(HOME_PROFILE_FILE) as f:
            return TRANSPORT if f.read().strip().lower() == TRANSPORT else PROPAGATION
    except OSError:
        return PROPAGATION


def save_home_profile(profile: str) -> str:
    profile = TRANSPORT if str(profile).strip().lower() == TRANSPORT else PROPAGATION
    try:
        os.makedirs(os.path.dirname(HOME_PROFILE_FILE), exist_ok=True)
        with open(HOME_PROFILE_FILE, "w") as f:
            f.write(profile)
    except OSError:
        pass
    return profile


@dataclass
class ModeResult:
    mode: str
    ok: bool
    steps: List[str] = field(default_factory=list)
    message: str = ""


def _set_kv_cmd(path: str, key: str, value: str) -> str:
    """A sed that rewrites the ACTIVE ``key = ...`` line (not commented copies),
    preserving its indentation. GNU sed ERE + POSIX classes for portability."""
    return (f"sed -i -E 's/^([[:space:]]*){key}[[:space:]]*=.*/"
            f"\\1{key} = {value}/' {path}")


def normalise(mode: str) -> str:
    return HOME if str(mode).strip().lower() == HOME else BACKPACK


def current_mode(connection: Connection) -> str:
    """The medic's current mode — from the persisted marker, else inferred from
    ``enable_transport`` in the RNS config (defaults to backpack if unreadable)."""
    m = connection.run(f"cat {MODE_FILE} 2>/dev/null")[1].strip().lower()
    if m in (HOME, BACKPACK):
        return m
    out = connection.run(f"grep -iE '^[[:space:]]*enable_transport' {RNS_CONFIG}")[1]
    return HOME if "yes" in out.lower() else BACKPACK


def set_mode(mode: str, connection: Connection, restart: bool = True,
             home_profile: Optional[str] = None) -> ModeResult:
    """Switch the medic between home and backpack: rewrite the config lines,
    persist the choice, and restart rnsd (+ lxmd).

    HOME turns transport ON; whether it *also* runs the LXMF propagation node
    depends on ``home_profile`` ('propagation' -> yes, 'transport' -> routing only),
    read from Settings when not given. BACKPACK turns both OFF. The transport edit
    is the critical one; the lxmd edit + restart are best-effort (lxmd may be
    absent). Returns a ``ModeResult`` describing what happened."""
    mode = normalise(mode)
    if home_profile is None:
        home_profile = load_home_profile()
    transport = "Yes" if mode == HOME else "No"
    propagation = "yes" if (mode == HOME and home_profile == PROPAGATION) else "no"
    res = ModeResult(mode=mode, ok=True)

    if connection.run(_set_kv_cmd(RNS_CONFIG, "enable_transport", transport))[0] == 0:
        res.steps.append(f"transport -> {transport}")
    else:
        res.ok = False
        res.steps.append("transport edit FAILED")

    if connection.run(_set_kv_cmd(LXMD_CONFIG, "enable_node", propagation))[0] == 0:
        res.steps.append(f"propagation -> {propagation}")
    else:
        res.steps.append("propagation edit skipped (no lxmd config)")

    connection.run(f"mkdir -p $(dirname {MODE_FILE})")
    connection.run(f"printf '%s' {mode} > {MODE_FILE}")

    if restart:
        if connection.run("sudo -n systemctl restart rnsd")[0] == 0:
            res.steps.append("rnsd restarted")
        else:
            res.ok = False
            res.steps.append("rnsd restart FAILED")
        if connection.run("sudo -n systemctl restart lxmd")[0] == 0:
            res.steps.append("lxmd restarted")
        else:
            res.steps.append("lxmd restart skipped")

    if res.ok:
        if mode == HOME and home_profile == PROPAGATION:
            res.message = ("Home mode — full propagation node (routing + message "
                           "store-and-forward). Stable infrastructure.")
        elif mode == HOME:
            res.message = "Home mode — transport node (routing only)."
        else:
            res.message = ("Backpack mode — transport OFF. Safe to move without "
                           "disturbing the network.")
    else:
        res.message = "Mode change hit a problem: " + "; ".join(res.steps)
    return res
