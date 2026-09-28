# Health replies through the mesh — the unicast reply (design, 2026-09-21)

Revised the same night after a design review (findings folded in; the
review's numbering is kept in brackets so the reasoning can be traced).

**Why.** "Ping node now" sends a one-byte request to a node's
`rtnode.health` destination; the node answers by *announcing* its health
beacon. The request travels the mesh like any packet, but the reply is an
announce, which relays rebroadcast on their own schedule and under a
per-interface announce cap — so the medic only ever hears an on-demand
reply from a node it can decode *directly*. The night ROOFRAK sat out of range (2026-09-21) every request was routed via ELSEWHERE and every
reply was lost; on a mesh that has grown past one house, on-demand
health silently stops working for every node beyond the medic's own
radio. Passive health (the node's periodic beacon announce, every 2 h)
does propagate and the medic already ingests relayed beacons — but two
hours is not "now". The operator's rule: "do things properly, no quick
fixes."

**What.** The request says *speak to me, about this*: it carries the
medic's return destination and a nonce. The node answers with a
**unicast packet** to that destination — self-identifying, bound to the
request by the nonce, **signed** with the node's identity — routed back
through the mesh like any message.

## Wire contract

Request (medic → node's `rtnode.health` SINGLE destination, encrypted to
the node as today):

| bytes | meaning |
|---|---|
| `0x04` | `HB_OPCODE_HEALTH_TO` — "send full health to the destination that follows" |
| 16 | the medic's reply destination hash (`nodemedic.health.reply`) |
| 8 | nonce, random per request |

`0x01` stays exactly as it is: the announce reply, for older firmware
and as the fallback. Unknown opcodes are dropped *before* the node's
rate limiter, so an old node that ignores `0x04` still answers a `0x01`.

Reply (node → the medic's reply destination, encrypted to the medic):

| bytes | meaning |
|---|---|
| 16 | the node's own `rtnode.health` destination hash — the lookup key [1] |
| 8 | the request's nonce, echoed [1][6] |
| N | the beacon, byte-for-byte the payload the announce carries |
| 64 | Ed25519 signature by the node's identity over `dest ‖ nonce ‖ beacon` |

The medic recalls the identity for the named destination (announces
already taught it), verifies the signature, and ingests the beacon under
that destination hash exactly as an announced one — with observation
source `"reply"` [9]. The hash is only a lookup key; the signature is the
authority, so a stranger naming another node's hash gains nothing. A
reply whose nonce matches no pending poll is verified and ingested as
fresh health but claims no poll; a reply whose `uptime_s` runs backwards
without a reboot is logged as stale and refused [6]. Sizes: request 25 B,
reply ≈117 B, against Reticulum's 383 B encrypted MDU.

## The node side

**Predicate [2][4]:** reply by unicast only when the node can *recall*
the medic's identity **and** *has a path* to the reply destination.
Otherwise announce now (today's reply — the medic hears it if it can),
and request a path so the *next* poll is unicast. Never block inside the
packet callback: the firmware's Reticulum port is single-threaded; the
Pi reporter replies from a worker thread and warms the path with a
short cap.

**Firmware (`HealthBeacon.h`, RTNode-2400-NM ≥ 0.7.0+nm.2):** opcode
`0x04` with 25 bytes → rate-limit check → `Identity::recall(dest)` and
`Transport::has_path(dest)` → build the beacon as `health_beacon_send`
does, sign `dest ‖ nonce ‖ beacon` with `Transport::identity()`, send
`Packet(Destination(identity, OUT, SINGLE, "nodemedic", "health.reply"),
payload)`. Two green pulses either way.

**Pi nodes (`monitor/pi_health_reporter.py`, ELSEWHERE / EVERYWHERE):**
the same handler in Python. Existing Pi nodes get the new reporter
without a rebirth: **Update health reporter** on the node's VITALS page
(REMOVED 2026-09-29 — see below) copied the package over the same SSH
road birth used, restarts `rnm-health`, and reads back that the handler
landed and the service is active before it says so [12]. Until a node is
updated its old reporter answers `0x04` with an announce — today's
behaviour. `scripts/rnm_health_ping.py` runs the same two-phase request
from a shell for bench proof when nobody is at the glass.

## The medic's reply destination

`RNS.Destination(identity, IN, SINGLE, "nodemedic", "health", "reply")`
under a dedicated persistent identity
(`~/.reticulum-node-medic/health_reply_identity`, written atomically,
mode 0600). The UI process owns it and announces it:

- on **every successful attach** to rnsd and every LocalInterface
  reconnect — an rnsd restart drops the hops-0 entry that makes rnsd hand
  inbound packets to the UI, and nothing else would restore it for ten
  minutes [3]; after announcing, the medic checks `rnpath -t --json` for
  its own destination at 0 hops and logs the result, so this is a check
  that can fail, not a hope;
- every 10 minutes otherwise, with **empty app_data** (a labelled
  persistent identity is a leak the transport announce does not already
  make) — and never within 60 s of a poll, because the medic's own
  announce is the likeliest thing to fill a relay's announce cap just
  when a fallback announce reply needs it [7].

The identity file joins `_OWN_IDENTITY_FILES`, and the medic's own
*destination hashes* are computed at start and applied in
`ingest_mesh` and `all()` as well — the path-table door, not only the
announce door, must be closed [5].

## The poll, hop-aware

1. Warm the path (unchanged). `rnpath` tells the hop count.
2. Send `0x04 ‖ reply ‖ nonce` to the node's **announced** health
   destination (the `_beacon_targets` entry, never a transport-identity
   guess) [10]. Wait **W1 = max(15 s + 5 s × (hops − 1), 32 s)** for a
   verified unicast reply *or* a fresh announce. The floor is the node's
   30 s rate limiter plus margin: a `0x01` sent sooner is swallowed [2].
3. Nothing → send `0x01`. Wait **W2 = 15 s + (hops − 1) × cap**, where
   cap is one relay's announce-cap hold — roughly announce airtime ÷ 2 %,
   ≈ 47 s at 1.8 kbps [7].
4. Nothing → amber, relay wording when hops ≥ 2.
5. **Keep listening [8].** A pending-poll table `{nonce → (dest, sent_at,
   hops, report)}` with a 5-minute TTL is consulted by the packet
   callback; a late verified reply records the probe as answered and
   re-reports if that node's page is still showing, else the sentence is
   kept the way the last-walk note is, not lost with the popup.

Signal wording: when hops ≥ 2 the RF the medic heard is the *relay's*
transmission and the sentence says so, so nobody re-aims the wrong
antenna [11].

## Beacon timer

Stays at 2 h. The review priced a 30-minute cadence: on a busy mesh the
rebroadcast cap could keep a relay's announce queue occupied for half of
every hour. Unicast makes the periodic cadence matter less; change it
only once airtime is measured, per WORKING_METHOD.

## Compatibility

| node | medic | result |
|---|---|---|
| NM ≥ nm.2, has path back | new | unicast reply in seconds, any hops |
| NM ≥ nm.2, no path back yet | new | announce reply now; path requested; next poll unicast |
| older NM / upstream firmware | new | `0x04` ignored, `0x01` answered by announce (W2) |
| Pi with old reporter | new | `0x04` answered by announce (any payload does); W1 catches it |
| any | old medic | unchanged |

## Tests

Pure: request/reply bytes, signature over `dest ‖ nonce ‖ beacon` with
real keys (stranger's key, tampered byte, wrong nonce, wrong dest all
refused), two overlapping polls answered in reverse order each attributed
to its own record, the node-side predicate (recall × has_path), the W1
floor (`t(0x01) − t(0x04) ≥ 30 s`), W2 per hop, pending-poll TTL, stale
uptime refusal, own-destination filter in the registry. Screen: pinned
wording. Firmware: builds for T114 (nRF52) and Heltec V3/EoRa (ESP32) on
the medic. Bench proof: ROOFRAK behind ELSEWHERE with the medic unable to
hear ROOFRAK directly — the exact case that failed — answers in seconds;
then `systemctl restart rnsd` on the medic and the same ping still
answers.

## Not in scope

Links; encrypting the beacon beyond what Reticulum already does;
changing the beacon format; the beacon cadence.

---

# Time over the mesh (design, 2026-09-23; revised the same day after two review lenses)

**Why.** A Pi propagation node on a solar bank dies overnight and boots
again in the sun with no correct time: no RTC, no internet in the field,
no NTP. A wrong clock corrupts the node's own logs and LXMF message
expiry, and misleads VITALS. Node Medic feeds the time to such nodes over
LoRa, several hops away, on the health channel above — three more
opcodes on the two destinations that already exist.

## Wire contract

All three ride the existing destinations; nothing about the health reply
changes.

**TIME_REQ** `0x06` — node → the medic's reply destination
(`nodemedic.health.reply`, the same one a `0x04` names). Exactly 25 bytes:

| bytes | meaning |
|---|---|
| `0x06` | ask for the time |
| 16 | the node's own `rtnode.health` destination hash |
| 8 | nonce, random per request |

**TIME** `0x05` — medic → the node's `rtnode.health` destination (next to
`0x01` and `0x04`). Exactly 81 bytes:

| bytes | meaning |
|---|---|
| `0x05` | the time follows |
| 8 | medic time, seconds since the epoch, uint64 big-endian |
| 8 | nonce — the TIME_REQ's when answering; fresh when pushing unasked |
| 64 | Ed25519 signature by the **medic's health-reply identity** over `node_dest ‖ 0x05 ‖ time ‖ nonce` |

The node's own destination is inside the signature, so a TIME captured for
one node is worthless replayed to another.

**TIME_ACK** `0x07` — node → the medic's reply destination. Exactly 98
bytes:

| bytes | meaning |
|---|---|
| `0x07` | what the node did |
| 16 | the node's `rtnode.health` destination hash |
| 8 | the TIME's nonce, echoed |
| 8 | what the node's clock read *before*, uint64 big-endian |
| 1 | **status** (below), signed raw |
| 64 | Ed25519 signature by the **node's** health identity over `node_dest ‖ 0x07 ‖ nonce ‖ before ‖ status` |

### The status byte

The first cut carried `applied: 1/0`, which could not say *why* a clock
was not moved — a failed helper and a clock that was already right both
read `0`. The byte is a status now; the length stays 98.

| status | meaning | the node page says |
|---|---|---|
| `0` | not needed — within `CLOCK_SLOP_S` | "checked … — already right" |
| `1` | the clock was set | "set by Node Medic over the mesh …" |
| `2` | the root helper failed or refused | "Node Medic sent the time … — the node could not set it" |
| `3` | refused: the node's clock is NTP-synchronised | "the node keeps NTP time — left alone" |
| `4` | refused: epoch not newer than the last one applied (the floor) | "checked …" — the node answered and did not move |

Compatibility: an old node sends only `0` and `1`, so an old medic never
sees a status it does not know from an old node. A new node's `2`/`3`/`4`
read as "not applied" on an old medic, which is true as far as it goes. A
status this medic does not know is still logged as the node's signed word.

**Telling them apart on the reply destination.** The callback hands every
packet to `monitor.time_service.TimeService.inbound` *before* verifying
anything, which answers in this order:

1. exactly 25 bytes with first byte `0x06` → TIME_REQ. A health reply is
   `node_dest[16] ‖ nonce[8] ‖ beacon ‖ sig[64]` — 88 bytes before its
   beacon — so no reply is 25 bytes; a request is never mistaken for one.
2. exactly 98 bytes with first byte `0x07`: if its nonce is a send this
   medic remembers (pending in memory, or the ledger's last send to any
   node — so an ack that lands after a UI restart still matches) → TIME_ACK;
   otherwise it gets its **own** log line, "TIME_ACK for a send this medic
   does not remember" — never "unverifiable health reply dropped".
3. shorter than `MIN_REPLY_LEN` → "too short to be a health reply and not
   a time packet", its own honest line.
4. anything else → the health reply, as before.

A finding worth recording: the shortest beacon the medic can decode is
`PAYLOAD_LEN` = 14 bytes, so the shortest reply it could ever verify *and*
decode is 16 + 8 + 14 + 64 = **102 bytes** (`MIN_REPLY_LEN`). Both time
packets on this destination — 25 and 98 bytes — sit below that floor, so
length alone already separates them from any usable reply. The reply's own
framing is untouched. Pinned in `tests/test_time_over_mesh.py`.

**Accuracy — an estimate, not a measurement.** A single unicast packet
crosses N hops and lands late. The figure used throughout, roughly
**1–3 s per hop**, is *estimated* from the ~1.8 kbps airtime of an 81-byte
packet plus per-relay processing; nothing has been timed on air. The node
applies the value as-is; no round-trip correction. ±hop-latency is fine for
logs, LXMF expiry and VITALS, which is why the node moves its clock only
when it is more than **30 s** off (`CLOCK_SLOP_S`): anything smaller would
be chasing the latency, not the truth.

## Freshness: the floor and the nonces

Three layers, from the outside in. Reticulum's transport refuses a
replayed packet by its packet-hash list — the first layer, and not ours.
At the protocol the node owns freshness twice over:

- **The monotonic floor.** `~/.rnm-health/time_state.json` (written
  atomically) keeps `last_applied_epoch`, the last TIME the node actually
  applied. A TIME whose epoch is not *greater* is refused with status `4`
  and no helper call — a captured TIME cannot wind the clock back, not
  even after a restart. Only a set (status `1`) raises the floor.
- **Issued nonces.** The asker remembers the nonces it sent (TTL 15 min,
  bounded at 32). A TIME whose nonce is one of them is logged as *asked*;
  any other is *pushed*. Both still pass the floor and the signature —
  the label is for the journal, not the decision.

## The node side (`monitor/node_time.py`, `monitor/pi_health_reporter.py`)

**Trust anchor.** The node accepts a TIME **only** from its trusted medic.
`~/.rnm-health/trusted_medic.json` = `{"identity_hash", "reply_dest",
"name", "since"}` is written at birth and by the reporter push. To verify:
`ident = RNS.Identity.recall(reply_dest)`; require `ident is not None and
ident.hash == identity_hash and ident.validate(sig, signed_bytes)`. No
trust file, or the identity not recalled yet (no announce heard from the
medic's reply destination) → the TIME is ignored and the journal says why
at NOTICE. Never unsigned time, never another signer.

**The order of the checks** (`node_time.handle_time`): trust → signature →
sanity (epochs before 2026-01-01 or from 2100-01-01 are refused, no ack) →
the floor (`4`) → **NTP** (`3`: a clock `timedatectl` says is synchronised
is *never* moved by a TIME; the reporter reads that state fresh, on the
worker thread, for each packet) → slop (`0`) → the helper (`1` or `2`).
Every outcome is one NOTICE line in the journal, with *asked*/*pushed*,
the medic's identity prefix and the delta.

**Privilege.** The reporter runs as the build user. The clock is set
**only** through `/usr/local/sbin/nm-settime` (root, 0755), invoked as
`sudo -n /usr/local/sbin/nm-settime <epoch>`. The helper insists on
exactly one argument of exactly 10 digits inside the same sane window,
runs `date -s @<epoch>` and prints `date -u` — nothing else.
`/etc/sudoers.d/nm-settime` (root, 0440) holds the one line `<user>
ALL=(root) NOPASSWD: /usr/local/sbin/nm-settime`.

### The re-ask cadence

The asker runs on **its own daemon thread** (`run_time_asker`) — it shells
out to `timedatectl` and warms paths, neither of which belongs on the
heartbeat. Every stamp it keeps is `time.monotonic()`: the whole point of
the node is that its wall clock gets *set*, and a backwards set once
silenced a heartbeat for the size of the jump. The heartbeat's own
deadline is monotonic for the same reason.

- After a 90 s grace on start (NTP may still fix a node that has internet),
  `timedatectl show -p NTPSynchronized` is re-read **every 10 min**
  regardless of the last answer — a node whose internet went with the sun
  said "yes" at noon and must be asked again at dusk. "yes" → no ask, and
  nothing else changes.
- Not synchronised (or unreadable, which is *not* "synchronised"): send a
  TIME_REQ every 10 min (`ASK_EVERY_S`) until a TIME is applied, and then
  — **no latch** — every 24 h (`RE_ASK_S`) for as long as NTP stays
  unsynced. The medic's push is opportunistic (below); the node's re-ask
  is what closes the loop.
- **Backoff:** three consecutive *identical* refusals (no trust file / the
  medic not recalled / no road) put the ask on an hourly cadence; the
  first success resets it. A node with no medic in range must not warm a
  path every ten minutes forever.
- A TIME_REQ goes out only when the trust file exists **and** the medic's
  identity is recalled **and** a path exists or can be warmed
  (`health_poll.warm_path`, exactly as the unicast reply does).
- If a TIME_ACK cannot be sent (no identity, no road, the send raised), it
  is kept and retried **once**, right after the next announce — the
  announce is what gives both sides a road.

## The medic side (`monitor/time_service.py`, `monitor/time_ledger.py`, `ui/app.py`)

The app delegates everything to `TimeService` (built in `build()`, never at
class body — a class-body ledger read the real home directory in every
test that imported the module) with the RNS module, the reply identity,
the registry and the clock-discipline check injected, so it is tested
behaviourally against fakes.

### The disciplined-clock rule

A signature makes the node *believe* the value; it does nothing to make it
*true*. The Pi 5 medic has no battery-backed RTC and boots to a bogus time
after a power loss — exactly the failure it is curing on the node. So the
medic signs **only a disciplined clock** (`monitor/medic_clock.py`): GPS
set it within the last 6 h (`gps_clock.last_disciplined_at`, stamped by
`apply_clock`) *or* `timedatectl` reports NTP synchronised — read **now**,
on the sender thread. Otherwise `send_time` refuses: NOTICE "refusing to
sign time for <node>: <reason>", `refused_at` + reason in the ledger, and
nothing on the air. The sanity window (`epoch_is_sane`) is checked as well.

### Answering a TIME_REQ

Only for a **known Pi kin record** — the registry's record for the named
destination must be kin with `node_type` starting `"pi"` (the registry's
Pi type is `"pi_propagation"`, from `kin_roster.type_for_cert`; older rows
say `"pi"`). Anything else is ignored with a rate-limited line. Then: one
answer per node per 60 s (`TIME_REQ_COOLDOWN_S`), at most **4** sender
threads in flight (`MAX_INFLIGHT_SENDS`; beyond that the request is dropped
with a rate-limited line), and the "answering" line itself is rate-limited
the way `_reply_reject_log` is — all of these are packets a stranger can
send. The sender: discipline → recall the node's identity → warm *this*
stack's path → sign → send; the ledger records the send **only after**
`.send()` returned without raising, with the nonce.

### The ack

Verified under the node's own recalled identity; matched to a pending send
by nonce **and** destination — or, when the pending table is gone (a UI
restart), to the ledger row whose last send carried that nonce. Recorded
with its status; the nonce is then spent, so a second ack carrying it
reads as "a send this medic does not remember". Pending nonces live in
their **own** bounded store (256 entries, 24 h TTL — not PendingPolls'
300 s: an ack crosses several hops after a path warm on the node's side).

### The opportunistic push

After any verified health reply from a Pi kin node, a TIME is pushed
unasked if no send **or try** went to that node in the last **6 h**
(`TIME_PUSH_EVERY_S`; `should_push` gates on `max(sent_at, tried_at)`, and
`tried_at` is stamped on the inbound thread *before* the sender starts, so
two replies seconds apart do not start two senders). Honesty about what
this is: a unicast reply today arrives **only when the operator taps
"Ping node now"** — the periodic beacon is an announce, not a reply — so
the push is a bonus that rides on an operator's action, not a schedule.
The node's own 24 h re-ask is what keeps a field node's clock right.

### The ledger and the node page

`~/.reticulum-node-medic/time_ledger.json` keeps, per node destination,
`{sent_at, sent_time, nonce, tried_at, acked_at, before, status, applied,
delta_s, why, refused_at, refused_why, sends_since_ack}`. The node page
(`ui/clock_line.py`, Pi nodes only — `is_pi_node(node_type)`, i.e.
`startswith("pi")`) looks the row up under **every destination of the
device's group** (`registry.consolidated_records`) and shows **one** line,
ages via the monitor's own `format_age_fine` (never a wall-clock HH:MM,
which reads as a different time once the medic's clock moves):

- "Clock: set by Node Medic over the mesh {age} ago (was {off} off)" — the
  "(was … off)" clause is dropped when the delta is unknown; nothing is
  invented;
- "Clock: checked by Node Medic {age} ago — already right";
- "Clock: Node Medic sent the time {age} ago — the node could not set it";
- "Clock: the node keeps NTP time — left alone";
- decay: an ack older than 24 h with a later send unanswered → "Clock: last
  confirmed {age} ago; {n} sends since unanswered" (or "…; a later try did
  not reach the node" when the try never became a send);
- never acked → "Clock: not yet confirmed by Node Medic".

`clock_state` never raises on a malformed row; every conversion is
wrapped. All strings are in the eight full catalogs.

## Birth and the push

### The birth step never fails; final_verification is the check

`install_time_trust` runs right after `install_health_reporter` and, like
it, **never fails the build**: it is step 10, `run_all` stops on a failed
step, and `hand_the_usb_port_back` comes later — a node born with a
working radio but no time trust is a node; one that cannot see its own
radio is not (WORKING_METHOD Part 3). On any failure the step returns
success with a message beginning "NOT installed — the node will not take
the time from this medic" and naming exactly what did not land.

The installer (`install_node_time_trust`, shared with the push) writes the
trust file from the medic's own anchor, the helper (0755 root) and the
sudoers line (staged, `visudo -c -f`, then moved in), and reads all three
back — content *and* `stat -c '%U:%G %a'`: helper `root:root 755`, sudoers
`root:root 440`. **Any failure unwinds the earlier stages** — a rejected
sudoers line removes the trust file and the helper too — so the node never
holds a trust file with no road to the clock. `visudo` absent (exit 127) is
reported as "could not be checked (visudo not found)", never as "rejected",
and the line is not installed without visudo's word.

`final_verification` on a Pi node reads the same three artefacts back the
same way and lists **"node will not take the time from Node Medic (…)"**
under its problems when they are missing or wrong; when the medic's anchor
cannot be loaded there, the trust file is checked for being a valid anchor
and "cannot say whether it names the medic" goes under *not checked*.

**The anchor is loaded, never minted.** `medic_time_anchor` uses
`RNS.Identity.from_file` on `monitor.health_reply.REPLY_IDENTITY_PATH`
(one literal, imported); when the file is absent it raises "this machine
holds no Node Medic health-reply identity — run this from the medic".
Only the UI's `_setup_health_reply` creates the identity. The reporter,
already running, loads the trust file lazily on each ask, so no restart is
needed.

**Update health reporter** (REMOVED 2026-09-29) shipped the
same three things plus `node_time.py` through the same installer. The user
and HOME the trust is written for come from the **unit** (`systemctl show
rnm-health -p User -p Environment`), falling back to `id -un` / `$HOME`
only when the unit is absent; after the restart it reads the trust back
again (owner and mode included) and its success message claims only what
read back.

## Multi-hop

Reticulum transport nodes forward a single unicast packet along a known
path, so the medic's TIME reaches a node three hops away the moment a
path exists — the path request goes out first, exactly as the `0x04` poll
does. What breaks it: no path (the node has not been announced through to
the medic, or the medic's reply destination has not been announced
through to the node — the node cannot even ask until it has recalled the
medic's identity from that announce); no transport-enabled node between
them (an end node does not forward); and the ~47 s per relay announce cap
above, which delays the *announces* the two sides need to learn each
other — not the time packets themselves, which are unicast.

## Out of scope

- nRF52 RTNode-2400 firmware nodes: no OS clock to keep.
- The medic's own time source: it has GPS and NTP (the disciplined-clock
  rule only asks whether either has spoken recently).
- Clones of the medic as additional trusted signers — v1.x: one trust
  file names one medic; a second medic must write its own anchor (which
  replaces the first).

## Tests

`tests/test_time_over_mesh.py` (wire with real Ed25519 keys and every
status, the four dispatch answers, the node's policy — floor, issued
nonces, NTP refusal, backoff, monotonic state — the reporter's handler,
asker loop and ack retry against a fake RNS, the ledger, the page line,
and the page's Pi condition through `kin_roster.type_for_cert` on a real
propagation certificate), `tests/test_time_over_mesh_app_wiring.py`
(`TimeService` driven behaviourally: discipline refusal, kin gate,
cooldown, in-flight cap, send-then-record, ack matching across a restart,
the orphan ack's and short packet's own lines, the push, the device-wide
lookup; then `ui/app.py`'s `_on_health_reply` compiled out of the file and
run against stubs), `tests/test_medic_clock.py`,
`tests/test_time_trust_build.py` (the never-failing step, the unwind,
visudo absent, `final_verification`'s read-back, the load-only anchor,
the helper under bash with a fake `date`, the push's unit-derived user),
`tests/test_phase_labels_i18n.py`. **Nothing here is proven on air** —
the bench proof is a Pi node with its clock deliberately wrong, behind a
relay, taking the time from the medic and its journal saying so.


## The push road was removed (2026-09-29)

`workflows/pi_reporter_push.py` and the **Update health reporter** button on a
node's page are both gone. The button was the only caller; briefly it became an
automatic check-and-push on opening a Pi node's page; then the operator settled
it:

> "Any boards that were birthed before the health updater will be reflashed, so
> we don't need to worry about that. And anybody who uses the Node Medic once
> it's been released won't have to worry about that issue either."

Birth installs the current reporter (`workflows/build.py`,
`install_health_reporter`), so every node this medic will meet already carries
the unicast handler and the time asker. The population the migration served is
empty.

What remains is birth's own time trust — `install_node_time_trust` and the
`nm-settime` helper — which the push merely reused; it is still installed and
still tested in `tests/test_time_trust_build.py`. The deleted module is in git
history if a stale reporter is ever found in the field.
