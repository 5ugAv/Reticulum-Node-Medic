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
the same handler in Python. Existing Pi nodes get the new reporter by a
"push reporter" step over the same SSH path birth used [12]; until then
an old reporter answers `0x04` with an announce, which is today's
behaviour.

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
