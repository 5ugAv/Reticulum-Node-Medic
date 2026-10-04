# CHAT — the medic's own messenger

*2026-09-29.* The front-page CHAT card opens a working messenger on the medic
itself: type a message, send it, read replies. Before this the card only
handed a phone the Columba/Sideband APK (that hand-off is still there — the
full-width "Put Columba or Sideband on a phone →" button under the chat list —
and under Settings ▸ Communication apps).

## Why not Sideband on the medic

Weighed and turned down. Sideband is a second full Kivy app: its own window,
its own identity, a large pip tree the medic does not carry offline, and a
layout that assumes a phone. One Kivy app already fills the five-inch panel.
LXMF — the protocol under Sideband, Columba and NomadNet — is already on the
medic (`lxmd` runs beside the UI as the propagation node), so CHAT is built on it
directly, in the medic's own theme, with the medic's own keyboard.

What was borrowed from the others, by reading their code:

| From | What |
|---|---|
| Sideband | request the path BEFORE sending; OPPORTUNISTIC (one packet, no link) when a ratchet is known and no link is up; re-announce every 90–300 min at random — LXMRouter has no periodic announce of its own |
| MeshChat | at start, anything still "sending" is marked failed; a failed message goes again the moment its peer announces |
| NomadNet | the hash sits beside the name — two phones can both say "Marnie" |

## The pieces

* `monitor/lxmf_chat.py` — the **store**: `~/.reticulum-node-medic/chat/messages.json`
  and `peers.json`, written atomically, mode 0600. Conversations, threads,
  unread counts, peer names. Pure Python; every mutation bumps `version`.
* `monitor/chat_service.py` — the **LXMF side**: one `LXMRouter` on the medic's
  ONE messaging identity (`~/.reticulum-node-medic/lxmf_identity`, the same
  file the operator-alert push has used since August — alerts and chat come
  from one address, and the alert now goes out through this service so two
  routers never fight over the identity). Starts on the mesh-listener thread
  once RNS is attached. `RNS`/`LXMF` are injectable; the tests drive a fake pair.
* `ui/screens/chat_screen.py` — the **screen**: list → thread → new, one widget,
  so the on-screen keyboard (which pans the ScreenManager) always reaches the
  field. Polls `store.version` once a second while open. Never touches RNS.

## How a message travels

1. Ask the mesh for a path (up to 12 s). No identity known for the address →
   **failed**, in words: *nobody on the mesh answered to that address. It goes
   again the moment they are heard.* (LXMF encrypts to the peer's identity; an
   address nobody has announced cannot be written to.)
2. Path known: **OPPORTUNISTIC** if a ratchet is known and no link is up (one
   packet), else a **DIRECT** link. Delivered → *delivered*.
3. No path, identity known: **PROPAGATED** to the medic's own `lxmd`
   (`~/.lxmd/identity` → its propagation hash), over the shared instance — no
   LoRa airtime. Shown as *waiting at propagation node (this medic's name)* —
   the name is the node holding the message.
4. A DIRECT/OPPORTUNISTIC failure falls back to step 3.

Inbound: the router's delivery callback → store (deduplicated by LXMF hash —
a post-office sync can re-deliver what a link already brought). The medic asks
its own propagation node for held messages at start and every 20 min; that request
is local.

## Airtime

One announce at start, then Sideband's random window. Path requests only when
sending. Opportunistic single packets for short messages. The post-office sync
never touches the radio. See [[bandwidth-economy-ethos]].

## Not yet

Attachments, read receipts, group chat, trust/tickets. (Deleting a conversation:
hold its row in the list.)
The two-phone test (Sideband ↔ medic ↔ Columba) is on the bench list.

## The propagation node and the Home/Backpack switch

Found 2026-09-30, fixed 2026-10-03. The medic's `lxmd.service` used to run
`lxmd -p -s`; `-p` forces the propagation node on **whatever `enable_node`
says**, so `workflows/node_mode.set_mode` — which writes `enable_node = no`
for Backpack and Home▸Transport-only — had never actually switched
propagation off. Until then that was convenient for chat (the propagation node
was always there) and wrong for the design (a roving Backpack medic should
not be a propagation node peers try to sync with).

**Done 2026-10-03 ~21:45 (operator paste, root):** the unit now runs
`lxmd -s`; `enable_node` — and so the front-page switch and the Home
profile — govern the propagation node. The install page reports what lxmd is
*doing* (`node_mode.propagation_running`), not what the mode meant.

**Home's default profile is Full propagation node** (Settings ▸ Home mode);
*Transport only* is an explicit choice, saved only when tapped. With
nothing saved, a fresh or cloned medic in Home mode is a propagation node:
routing on, store-and-forward on. In Backpack the propagation node is off and
CHAT's "waiting at propagation node" road is closed until the medic is home
again — the screen says so (*no path right now*).

## Proven (2026-10-01)

First live exchange, over LoRa. Phone: Columba on Android, a Heltec
MeshPocket as its RNode over Bluetooth, fleet radio parameters. The phone's
announce arrived as `5a150015…` / display name
"5ugAv" and the medic listed it under *Heard on the mesh*; medic → phone
"your node medic pal" 23:28:28 → `delivered` (double tick in Columba);
phone → medic "It works!" 23:29:27, in the store as `in … delivered`.
rnsd: 1 hop via RNodeInterface.

Two lessons from it, both fixed the same night:

* The address Columba shows most prominently was NOT the LXMF delivery
  address (the operator typed `5a9abc75…`; nothing answers to it). The one
  that matters is the one the phone ANNOUNCES — so "Heard on the mesh" is
  the road to use, and a typed address is no longer listed as "heard".
* The thread's route line said "1 hop via Local shared instance": the UI is
  a client of rnsd and sees only its own next hop. `peer_route` now reads
  rnsd's path table for the real interface.
