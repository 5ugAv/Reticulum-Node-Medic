# Which node should I build?

Three kinds of node do three different jobs. Pick by what the node is FOR, not
by what looks best on a shelf.

---

## 1. The everyday node — as many as you can make

**Build:** XIAO ESP32-S3 + Wio-SX1262 kit, as an RTNode-2400 (the medic builds it)
**Cost:** about AU$21 each in Australia (US$11 from the maker, before postage);
no antenna or case in the box
**Power:** mains, USB power bank, or solar with a real panel — the ESP32 is
not a low-power part

* This is the node most of a network should be made of.
* It routes and relays exactly as well as a AU$60 board — you give up battery
  life, a screen, a case and GPS, not networking.
* Antenna is free: 8.2 cm of wire (see docs/CHEAPEST_NODE.md).
* For people who solder: a bare ESP32 devkit + RFM95 module is about AU$7 in
  parts — full list and wiring in **docs/CHEAPEST_NODE.md** — but the medic
  cannot flash that build for you yet.

## 2. The remote node — put it somewhere and leave it

**Build:** an nRF52840 + SX1262 board **on its own**, as an RTNode-2400
**Boards:** RAK4631 kit (proven here; the lowest-power class the medic builds),
Heltec Mesh Node T114 (proven here; an import — no Australian shop stocks it).
Heltec Mesh Solar and Seeed SenseCAP Solar Node exist but the medic has not
built either.
**Cost:** about AU$40 for the RAK4631 kit; AU$26–60 across boards
**Power:** small battery, small panel — weeks to months unattended

* The radio board IS the node. It needs no computer attached.
* nRF52840 sleeps at microamps and wakes for packets; listening costs about
  5–10 mA. On one 18650 that is weeks; with a small panel it simply never
  stops (18650 + holder about AU$14; 6 W panel + 1S solar charger about AU$43).
* **Do NOT bolt a Raspberry Pi to one of these to "save battery".** A Pi draws
  10–20× what the radio does, so you would pay for a low-power radio and then
  throw the low power away. That pairing is both the most expensive build and
  the shortest-lived on a battery.
* The purpose-built solar ones (Mesh Solar, SenseCAP Solar Node) arrive with
  the panel, battery and weatherproofing already solved — worth the money if
  the node is going up a pole.

## 3. The message-holder — for people who are offline

**Build:** Raspberry Pi + an nRF52 radio board (RAK4631 is the proven pairing)
**Cost:** about AU$100–155 on mains with a Pi Zero 2 W (often sold out);
AU$230 and up with a Pi 4 or 5
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

* **Pi Zero 2 W** — the only Pi to use for solar. Measured with a RAK4631:
  0.142 A at 5.06 V = 0.72 W, about 17 Wh per day (19–20 Wh drawn from the
  battery once the 12→5 V converter's losses are counted).
* **Panel: 20 W minimum** — 40 W in Canberra or Hobart. A 5 W panel matches a
  *perfect* day and fails the first cloudy one. 20 W buys the margin that keeps
  it alive through winter and dust.
* **Battery: 3 days of autonomy — about 60 Wh USABLE.** A 10–12 Ah 12 V sealed
  lead-acid (only half of it is usable), or about 6 Ah of 12 V LiFePO4. A 7 Ah
  SLA gives two days. A power bank is not a battery for this job unless it can
  charge and discharge at once (most cannot).
* **A real charge controller.** A solar panel wired straight to a power bank is
  how batteries die. A small MPPT or PWM controller sized for the panel.
* **Undervoltage kills Pis quietly.** Use a supply and cabling that hold 5 V
  under load; a sagging rail corrupts SD cards and has taken out a USB
  controller on this very bench.
* **Expect AU$265–470 all up** for a Pi node that genuinely survives on solar:
  20 W panel ~AU$50, controller AU$25–70, battery AU$35–105, converter AU$15,
  box AU$40–65, wiring AU$15–25, plus the node itself.
  If mains power is available anywhere nearby, use it — the money is better
  spent on more everyday nodes.

---

## The short version

* **Most nodes** → XIAO ESP32-S3 kit, about AU$21, mains or power bank
* **Remote and unattended** → nRF52 + SX1262 *alone* (RAK4631 kit, about
  AU$40), battery/solar
* **Holding messages for offline people** → Pi Zero 2 W + RAK4631, AU$100–155
  on mains, or AU$265–470 done properly on solar
* Prices: Australian dollars before postage, checked October 2026 — see
  docs/BOARD_SHOPPING_LIST.md
* **Never** → an expensive low-power radio bolted to a Pi and called a battery
  node

## Whatever you build

* Right band: 915 MHz for Australia/NZ/US, 868 for Europe. A 433 MHz module
  looks identical and will never join your network.
* An antenna before you power it — transmitting bare damages the radio.
* 3.3 V to the radio. Never 5 V.
* One Node Medic can flash, birth and repair every node here. A community needs
  one medic and as many nodes as it can build.
