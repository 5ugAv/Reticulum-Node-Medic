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
(`workflows/pi_reporter_push.py`) copies the package over the same SSH
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

# Time over the mesh (design, 2026-09-23)

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
| 1 | applied: `1` the clock was set, `0` it was not |
| 64 | Ed25519 signature by the **node's** health identity over `node_dest ‖ 0x07 ‖ nonce ‖ before ‖ applied` |

**Telling them apart on the reply destination.** The callback dispatches
*before* it verifies anything, in this order:

1. exactly 25 bytes with first byte `0x06` → TIME_REQ. A health reply is
   `node_dest[16] ‖ nonce[8] ‖ beacon ‖ sig[64]` — 88 bytes before its
   beacon — so no reply is 25 bytes; a request is never mistaken for one.
2. exactly 98 bytes with first byte `0x07` **and** its nonce is a TIME the
   medic is still waiting on → TIME_ACK. A 98-byte `0x07` whose nonce
   matches no pending send goes down the reply path, where it fails
   verification like any stranger's bytes.
3. anything else → the health reply, as before.

A finding worth recording: the shortest beacon the medic can decode is
`PAYLOAD_LEN` = 14 bytes, so the shortest reply it could ever verify *and*
decode is 16 + 8 + 14 + 64 = **102 bytes** (`MIN_REPLY_LEN`). Both time
packets on this destination — 25 and 98 bytes — sit below that floor, so
length alone already separates them from any usable reply; the
pending-nonce check in step 2 is the belt to that brace. The reply's own
framing is untouched. Pinned in `tests/test_time_over_mesh.py`.

**Accuracy.** A single unicast packet crosses N hops at ~1.8 kbps and
lands seconds late — roughly **1–3 s per hop**. The node applies the value
as-is; no round-trip correction. ±hop-latency is fine for logs, LXMF
expiry and VITALS, which is why the node moves its clock only when it is
more than **30 s** off (`CLOCK_SLOP_S`): anything smaller would be chasing
the latency, not the truth.

## The node side (`monitor/node_time.py`, `monitor/pi_health_reporter.py`)

**Trust anchor.** The node accepts a TIME **only** from its trusted medic.
`~/.rnm-health/trusted_medic.json` = `{"identity_hash", "reply_dest",
"name", "since"}` is written at birth and by the reporter push. To verify:
`ident = RNS.Identity.recall(reply_dest)`; require `ident is not None and
ident.hash == identity_hash and ident.validate(sig, signed_bytes)`. No
trust file, or the identity not recalled yet (no announce heard from the
medic's reply destination) → the TIME is ignored and the journal says why
at NOTICE. Never unsigned time, never another signer.

**Sanity.** Epochs before 2026-01-01 or from 2100-01-01 are refused.
Within 30 s the node acks `applied=0` and logs "clock already within
30 s"; beyond it, the clock is set and the ack says `applied=1` with what
the clock read before.

**Privilege.** The reporter runs as the build user. The clock is set
**only** through `/usr/local/sbin/nm-settime` (root, 0755), invoked as
`sudo -n /usr/local/sbin/nm-settime <epoch>`. The helper insists on
exactly one argument of exactly 10 digits inside the same sane window,
runs `date -s @<epoch>` and prints `date -u` — nothing else.
`/etc/sudoers.d/nm-settime` (root, 0440) holds the one line `<user>
ALL=(root) NOPASSWD: /usr/local/sbin/nm-settime`.

**Asking.** On reporter start, after a 90 s grace (NTP may still fix a
node that has internet), and then every 10 min while `timedatectl show -p
NTPSynchronized --value` says `no` (or cannot be read), the node sends a
TIME_REQ — only when the trust file exists **and** the medic's identity is
recalled **and** a path exists or can be warmed (`health_poll.warm_path`,
exactly as the unicast reply does). It stops once the medic's TIME was
applied or NTP reports synchronised. A TIME arriving unasked (a medic
push) is handled the same way. Every request, refusal and outcome is in
the node's journal at NOTICE — journald drops VERBOSE (lesson 2026-09-22).

## Birth and the push

`install_time_trust` runs right after `install_health_reporter` — its own
step, because that step deliberately never fails (a failed step strands
the USB hand-back) and this one **must**: it writes the trust file from
the medic's own health-reply identity (hash and reply-destination hash
computed with `RNS.Destination.hash(identity, "nodemedic", "health",
"reply")`; if RNS is unavailable on the medic at build time the step says
so and fails — never a guessed hash), installs the helper (0755 root) and
the sudoers line (staged, `visudo -c -f`, then moved in — a rejected file
is removed and the step fails), and reads all three back. The reporter,
already running, loads the trust file lazily on each ask, so no restart is
needed. **Update health reporter** (`workflows/pi_reporter_push.py`) ships
the same three things plus `node_time.py` through the same installer and
the same read-backs.

## The medic side (`ui/app.py`, `monitor/time_ledger.py`)

TIME_REQ → answered with a signed TIME once the node's identity is
recalled and a path is warmed (the two-phase pattern of `_ping_node`, off
the inbound thread). TIME_ACK → verified under the node's key, bound to
the pending send by nonce and destination, recorded. After any verified
health reply, a TIME is pushed unasked if none went to that node in the
last **6 h** (`TIME_PUSH_EVERY_S`); the node ignores it when within 30 s
and acks `applied=0`. The medic will not offer a time when its own clock
reads before 2026 (the node would refuse it anyway; this saves the
airtime). Everything is logged at NOTICE with the delta.

`~/.reticulum-node-medic/time_ledger.json` keeps, per node destination,
`{sent_at, sent_time, acked_at, before, applied, delta_s, why}`. The
node page shows **one** line from it, Pi nodes only, and claims a set only
from an ack with `applied=1`:

- "Clock: set by Node Medic over the mesh at HH:MM (was N h off)"
- "Clock: checked by Node Medic at HH:MM — already right"
- "Clock: not yet given by Node Medic" — including a TIME sent and never
  acked.

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
- The medic's own time source: it has GPS and NTP.
- Clones of the medic as additional trusted signers — v1.x: one trust
  file names one medic; a second medic must write its own anchor (which
  replaces the first).

## Tests

`tests/test_time_over_mesh.py` (wire with real Ed25519 keys, dispatch
order, the node's policy, the reporter's handler and asker against a fake
RNS, the ledger, the page line), `tests/test_time_trust_build.py` (the
birth step and the push against the build harness, the helper run under
bash with a fake `date`, the sudoers line under visudo where present),
`tests/test_time_over_mesh_app_wiring.py` (the app's dispatch and send
pinned in source). **Nothing here is proven on air** — the bench proof is
a Pi node with its clock deliberately wrong, behind a relay, taking the
time from the medic and its journal saying so.
