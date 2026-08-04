# The card that ships with a sold RNode

A physical insert for hardware sales. It does two jobs at once: it satisfies
GPLv3 §6 (notices, attribution, source offer, installation information), and it
tells the buyer what they have actually bought — which is not a gadget but a
piece of a network they now co-own.

**Not legal advice.** Get an opinion before the first paid sale.

---

## Front

> ### This is an RNode
>
> A LoRa radio that speaks **Reticulum** — a network that needs no towers, no
> subscriptions and no accounts. Pair it with a phone or a computer and you can
> send messages over the air, directly, with no internet involved.
>
> Reticulum *can* travel over the internet when a link is there; the point is
> that it doesn't have to. When the power's out, when the lines are down, when
> you're simply out of range — the radio still carries it. Everything is
> encrypted end to end, whichever way it travels.
>
> **You'll need an app.** The radio is the transport, not the messenger — pair it
> with **Sideband** on Android, or **MeshChat** / **Sideband** on a computer.
> Both are free.
>
> It works better with neighbours. Every node someone adds makes the network
> stronger for everyone already on it.
>
> **Think globally. Act locally.**

---

## Back

> ### It's yours — including the software
>
> This device runs free software, and that is not a technicality. You are
> entitled to read it, change it, and put your own version on this hardware.
> Nothing here is locked.
>
> **Install your own firmware.** Plug it into a computer over USB and flash it.
> No signing keys, no locked bootloader, no secure-boot fuses burned. If you
> want to change what it does, you can.
>
> **The exact source for the firmware on this unit:**
>
> ```
> <SOURCE_URL>
> build tag: <BUILD_TAG>
> ```
>
> That tag matches the firmware on THIS device — not a later version. If the
> page ever goes down, write to <CONTACT> and I will send you the complete
> source on media at no more than the cost of posting it. **That offer stands
> for three years** from the date you bought this.
>
> **Built on the work of others**, and licensed **GPL-3.0**:
>
> - **Reticulum** and **RNode Firmware** — Mark Qvist
> - **microReticulum** (Reticulum in C++, small enough for this chip) —
>   Chris Attermann
> - **RNode Firmware CE** — Liberated Systems & contributors
> - **RTNode for Heltec V4** — jrl290
> - **RTNode-2400** — GrayHatGuy
> - Assembled, cased and tested by **<YOUR_NAME>**
>
> A full copy of the GNU General Public License v3 is included with the source
> at the link above, and on the card insert overleaf / at `<LICENCE_URL>`.
>
> If this is useful to you, consider supporting the upstream authors — they gave
> the work away so that networks like this could exist.

---

## Printing notes

- **Size**: A6 (105 × 148 mm) or business-card stock folded once. The back must
  stay legible — do not shrink the source URL to fit a smaller card.
- `<SOURCE_URL>`, `<BUILD_TAG>`, `<CONTACT>`, `<YOUR_NAME>` and `<LICENCE_URL>`
  are filled per batch. The build tag is the part that must not be guessed —
  see below.
- The GPL text itself does not have to be printed on the card; it must
  *accompany* the product. Including it in the source archive and naming where
  it is satisfies that, but a QR to the licence costs nothing.

**Keep the front honest when you edit it.** An earlier draft said the buyer
could "send messages that never touch the internet". That is not true and a
knowledgeable buyer would catch it: Reticulum routes over the internet quite
happily where a link exists — TCP interfaces, propagation nodes with uplinks.
The true and better claim is that it does not NEED one. Overselling the radio is
the one thing that would undermine a card whose whole purpose is to be
straight with the person holding it.

## The record-keeping that makes this true

The offer above is only honest if the tag really does correspond to the shipped
binary. GPLv3 requires the source for **that** unit, not the latest.

Node Medic already records a firmware version and hash on every birth
certificate. Use it:

1. Tag the firmware fork for every build that ships — `v1.4.0-heltec-v4`, not
   "main".
2. Put `<SOURCE_URL>` and `<BUILD_TAG>` on the birth certificate as well as the
   card, so the unit and the paperwork agree.
3. Keep a sales log of serial → build tag → date. Three years is longer than
   memory.
4. Never force-push or delete a shipped tag. A tag someone paid for is a
   promise.

## What you do NOT owe

- **The case.** Your 3D design is industrial design and your copyright. GPL
  covers software.
- **A fee to anyone.** Charge what the hardware is worth. No royalty, no
  permission needed.
- **The Node Medic tool.** It compiles and flashes the firmware at arm's length;
  it is a separate program and stays under its own licence.

## What you must NOT do

- **Do not point buyers at unsigned.io for source.** Corresponding Source means
  the source that rebuilds *your* binary, including your modifications and build
  configuration. Upstream cannot do that, and it puts your obligation onto
  someone else's shoulders.
- **Do not lock the hardware.** Burning secure-boot or flash-encryption eFuses,
  or shipping a bootloader that refuses unsigned images, breaches GPLv3 §6 no
  matter how much source you publish. This is the one obligation that is decided
  at manufacture and cannot be fixed afterwards.
