# Which node should I build?

Three kinds of node do three different jobs. Pick by what the node is FOR, not
by what looks best on a shelf.

---

## 1. The everyday node — as many as you can make

**Build:** classic ESP32 devkit + RFM95/SX1276 module
**Cost:** about AU$7 each (AU$5 in tens)
**Power:** mains, USB power bank, or solar with a real panel — the ESP32 is
not a low-power part

* This is the node most of a network should be made of.
* It routes and relays exactly as well as a $60 board — you give up battery
  life, a screen, a case and GPS, not networking.
* Antenna is free: 8.2 cm of wire (see docs/CHEAPEST_NODE.md).
* Full parts list and wiring: **docs/CHEAPEST_NODE.md**

## 2. The remote node — put it somewhere and leave it

**Build:** an nRF52840 + SX1262 board **on its own**, as an RTNode-2400
**Boards:** RAK4631, Heltec Mesh Node T114, Heltec Mesh Solar, Seeed SenseCAP
Solar Node
**Cost:** AU$30–60
**Power:** small battery, small panel — weeks to months unattended

* The radio board IS the node. It needs no computer attached.
* nRF52840 sleeps at microamps and wakes for packets; listening costs about
  5–10 mA. On one 18650 that is weeks; with a 2 W panel it simply never stops.
* **Do NOT bolt a Raspberry Pi to one of these to "save battery".** A Pi draws
  10–20× what the radio does, so you would pay for a low-power radio and then
  throw the low power away. That pairing is both the most expensive build and
  the shortest-lived on a battery.
* The purpose-built solar ones (Mesh Solar, SenseCAP Solar Node) arrive with
  the panel, battery and weatherproofing already solved — worth the money if
  the node is going up a pole.

## 3. The message-holder — for people who are offline

**Build:** Raspberry Pi + any supported radio
**Cost:** AU$60+
**Power:** mains, or a PROPERLY SIZED solar install — see below

* Only this kind of node can hold messages for someone whose device is
  switched off (LXMF store-and-forward). An RTNode-2400 cannot: its embedded
  Reticulum is core routing only.
* A network wants a few of these, not many. One in a hall, a shop, a home with
  power — somewhere it can just stay on.

### Putting a Pi node on solar — what it actually takes

A Pi is a computer that never sleeps. "Solar" for a Pi is not the same word as
"solar" for an nRF52 node, and undersizing it is the usual reason these die in
the first fortnight.

* **Pi Zero 2 W** — the only Pi to use for solar. About 0.12 A idle, so roughly
  15 Wh per day.
* **Panel: 20 W minimum.** A 5 W panel matches a *perfect* day and fails the
  first cloudy one. 20 W buys the margin that keeps it alive through winter and
  dust.
* **Battery: 3 days of autonomy — about 45 Wh.** That is roughly four 18650
  cells, or a 20,000 mAh power bank that can charge and discharge at once
  (many cannot — check before buying).
* **A real charge controller.** A solar panel wired straight to a power bank is
  how batteries die. A small MPPT or PWM controller sized for the panel.
* **Undervoltage kills Pis quietly.** Use a supply and cabling that hold 5 V
  under load; a sagging rail corrupts SD cards and has taken out a USB
  controller on this very bench.
* **Expect AU$120–200 all up** for a Pi node that genuinely survives on solar.
  If mains power is available anywhere nearby, use it — the money is better
  spent on more everyday nodes.

---

## The short version

* **Most nodes** → ESP32 + RFM95, AU$7, mains or power bank
* **Remote and unattended** → nRF52 + SX1262 *alone*, AU$30–60, battery/solar
* **Holding messages for offline people** → Pi + radio, AU$60+ mains, or
  AU$120–200 done properly on solar
* **Never** → an expensive low-power radio bolted to a Pi and called a battery
  node

## Whatever you build

* Right band: 915 MHz for Australia/NZ/US, 868 for Europe. A 433 MHz module
  looks identical and will never join your network.
* An antenna before you power it — transmitting bare damages the radio.
* 3.3 V to the radio. Never 5 V.
* One Node Medic can flash, birth and repair every node here. A community needs
  one medic and as many nodes as it can build.
