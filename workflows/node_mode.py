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

from dataclasses import dataclass, field
from typing import List

from transport.connection import Connection

RNS_CONFIG = "~/.reticulum/config"
LXMD_CONFIG = "~/.lxmd/config"
#: Persisted preference so the UI shows the mode + it survives an app restart.
MODE_FILE = "~/.reticulum-node-medic/node_mode"

HOME, BACKPACK = "home", "backpack"
#: (transport value, propagation value) per mode — RNS wants Yes/No, lxmd yes/no.
_SETTINGS = {
    HOME:     {"enable_transport": ("Yes", RNS_CONFIG), "enable_node": ("yes", LXMD_CONFIG)},
    BACKPACK: {"enable_transport": ("No", RNS_CONFIG),  "enable_node": ("no", LXMD_CONFIG)},
}


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


def set_mode(mode: str, connection: Connection, restart: bool = True) -> ModeResult:
    """Switch the medic between home and backpack: rewrite the config lines,
    persist the choice, and restart rnsd (+ lxmd). The transport edit is the
    critical one; the lxmd/propagation edit + restart are best-effort (lxmd may be
    absent on a given build). Returns a ``ModeResult`` describing what happened."""
    mode = normalise(mode)
    res = ModeResult(mode=mode, ok=True)

    tv, tpath = _SETTINGS[mode]["enable_transport"]
    if connection.run(_set_kv_cmd(tpath, "enable_transport", tv))[0] == 0:
        res.steps.append(f"transport -> {tv}")
    else:
        res.ok = False
        res.steps.append("transport edit FAILED")

    nv, npath = _SETTINGS[mode]["enable_node"]
    if connection.run(_set_kv_cmd(npath, "enable_node", nv))[0] == 0:
        res.steps.append(f"propagation -> {nv}")
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
        res.message = ("Home mode — routing + message store-and-forward ON. "
                       "The medic is stable infrastructure now."
                       if mode == HOME else
                       "Backpack mode — transport OFF. Safe to move without "
                       "disturbing the network.")
    else:
        res.message = "Mode change hit a problem: " + "; ".join(res.steps)
    return res
