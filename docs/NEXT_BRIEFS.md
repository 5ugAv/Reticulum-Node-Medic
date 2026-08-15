# Ready-to-run briefs

Written 2026-08-15. Four pieces of work, specified in enough detail to hand
straight to an agent. **They are ordered because they conflict** — items here
touch `workflows/build.py`, `ui/screens/birth_screen.py` and `monitor/registry.py`,
and the four worktree merges must land before any of them start.

Standing rules for all of these: worktree isolation, tests first and about the
failure modes, full suite green, no deploy without the operator, and
`docs/WORKING_METHOD.md` read first.

---

## A. Restore trusted address clearing in `forget_reimaged_node`

**Blocks every rebirth. Do this first.**

`provisioning/host_keys.forget_reimaged_node()` used to resolve a node's name and
clear stale SSH host keys under its **LAN address** too. That was removed on
2026-08-11 on a real security finding: `getaddrinfo` is unauthenticated, so
anyone able to answer a name query could choose which host keys the medic
forgot — and `accept-new` silently trusts an UNKNOWN key while refusing a
CHANGED one, so a deletion turns the alarm off for a host an attacker picked.

The security reasoning holds. **The operational cost is that a re-imaged node's
stale LAN key is never cleared**, so `detect_hardware` fails with "The Pi's SSH
identity has changed" on every rebirth. Observed live on 2026-08-15 rebirthing
EVERYWHERE at `192.168.1.42`, three attempts in a row.

**Build:** clear keys for addresses from a TRUSTED source only — the addresses
recorded in that node's own birth certificate, and the address the medic itself
probed and pinned during this build. Never a bare name lookup. The `resolver`
argument is the existing seam and is currently `None`.

**Test:** a certificate-derived address IS cleared; a name-lookup-derived one is
NOT; a node with no certificate degrades to name+cable only.

---

## B. Pass the radio's serial through to the build

**Makes `/dev/rnode` mean *this* radio, and makes a screen stop lying.**

The guided flow flashes the radio on the medic at step 0, has the operator
REMOVE it at step 2, and provisions at step 6 — so on every Pi birth the radio
is absent during the build. Therefore `attached_radio_serial()` runs `udevadm`
on a device that does not exist, returns `""`, and **the five-vendor fallback
udev rule ships every time**. Any FTDI or CH340 device later plugged into that
node can claim `/dev/rnode`.

The medic HAD the serial: `RNodeFlashWorkflow._verify` captures it into the
radio's birth certificate at step 0. It is discarded because
`birth_screen._hand_back_to_guide` passes only
`{radio_verified, build_failed, reached_at}`.

**Build:** carry the serial through the hand-off into the build profile, and
have `install_radio_rule` prefer it over the vendor fallback. Keep the fallback
for the case where it genuinely is unknown, and keep the rule file saying which
one it used.

**Also:** step 1 of the Pi path claims the medic "remembers which radio it is so
the Pi finds it later". Today that is FALSE. Once this lands it becomes true —
check the wording survived the wording merges, and make it accurate either way.

---

## C. Steps that cannot fail

A step whose failure is swallowed reports success that means nothing.

- `apply_system_hardening` (`workflows/build.py`) — `|| true` throughout, and it
  claims to configure **log rotation, which it never touches**. Always green.
- `install_health_reporter` — module pushes, `daemon-reload`, `enable` and
  `start` all unchecked. **Never runs `systemctl is-active rnm-health`.**
- `configure_services` — enable/start results discarded; `lxmd`, the component
  that makes it a propagation node, is never verified.
- `install_radio_rule` — writes the rule, never reads it back (unlike
  `hand_the_usb_port_back`, which exists because of exactly this class of bug).
- `write_reticulum_config` — checks rc, does not read back. The one file the
  whole node depends on.
- **`final_verification` passes on a MUTE node** — it asks "is rnsd active?" and
  "does the config exist?", and with `panic_on_interface_error = No` rnsd stays
  up holding a dead interface. This is how two nodes shipped mute on 2026-08-10.

**Build:** each step verifies its own outcome, and `final_verification` checks
the radio is actually there and open. **Test that each step CAN fail** — a test
that a step reports success is not a test that it did anything.

---

## D. Two controls that exist and are never called

**D1 — the impossibility gate.** `ui/pi_connectors.can_cable()` and
`provisioning/cable_birth.supports_cable_birth()` both encode tested hardware
facts and **neither has a production caller**. The only gate before the medic
writes a card is a power check. So a Pi 3 B+ — which physically cannot present a
USB gadget, because a hub sits between the SoC and every port — is offered in
the picker, gets a card baked, and is walked through all 8 steps, waiting 150 s
at step 6 for an enumeration that cannot happen. Its impossibility appears once,
as hint text, under a title that still says "connect it to Node Medic".

Call it before the card is written. Offer the Wi-Fi route instead, which already
exists. (Task #88.)

**D2 — forget a node from VITALS.** The operator asked twice and it was never
built. A slide-to-confirm control (like `ui/widgets/gear_shift.py` /
`slide_to_power.py`), on node detail, deliberately hard to trigger on a
bag-carried touchscreen.

**It must be forget-AND-IGNORE, not row removal.** Proven on 2026-08-11: the
registry was wiped from 62 nodes to 0, and rebuilt itself to 12 within minutes
from live mesh traffic. A delete that only removes the row appears to do
nothing. Suppress the destination until un-ignored, and say on screen that this
removes the medic's records only — it does not touch the node, and a live node
will come back if un-ignored.

---

## E. Recommended, not yet specified

BIRTH warns about cables in four places and **never about the medic's own power
rail**, which "presents exactly like a bad cable". `monitor/self_diagnose.check_throttled`
already exists. Wiring it into the radio/node gates would stop an operator
chasing a third cable when the rail is sagging. Less urgent since the 5 A supply
landed, but the diagnosis is still missing.
