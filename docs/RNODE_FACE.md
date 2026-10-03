# The RNode face — how a Node Medic build is told from a stock RNode

*2026-10-03.* Every RNode the medic births from `~/RNode_Firmware` (branch
`medic-cross`) shows Mark Qvist's OLED face **unchanged** — the big RNODE
logo, the hash tail phones pair against, the cycling hardware-OK / online /
version cards, the Bluetooth pairing-PIN card, the fault cards, and the
whole right-hand instrument panel with its waterfall — except the 6-px
header strip, which read `unsigned.io` and now reads **NODE MEDIC** with a
lit medical cross at its end.

(the photographed face is not reproduced here: a real board's hash tail was readable in it)

That strip is the only tell, and it is enough: a stock RNode and a
Node-Medic-built one (health beacon, neighbour report, unicast reply,
command-surface guard) look identical otherwise, and a stranger with a
dead box in their hand needs to know which it is before they do anything.

## Why not a new face

Three redesigns were mocked up (a breathing "host pulse" ring, the poster's
cross-in-circle, a radar sweep, each under an `RNode` wordmark cut in the
front page's heavy square face). The operator looked at the real stock
screen and chose against them: *"there's a lot of function in the
original … let's just add a little cross."* The upstream face carries more
working information per pixel than any of ours did; the airtime-of-
attention argument that applies to LoRa applies to a 0.96" screen too.

## Where the bytes live

`Graphics.h` only — `bm_def_lc` and `bm_def` (64×23, 1 bit, MSB-first rows).
Rows 0–5 are the inverted strip: x 0–52 white with NODE MEDIC in black 3×5
glyphs, x 53–63 a dark pocket with a lit 2-px-arm plus. No `Display.h`
change. Compiles for `heltec32_v4` at 69 % of flash. The upstream credit
remains in every source header and on the medic's credits screen.

## Not yet

* Seen on a physical OLED — the bench step (birth a V3/V4, photograph it).
* Pushed to GitHub (`git push origin medic-cross` from `~/RNode_Firmware`;
  the commit already carries the noreply author).
* The **MeshPocket** and **EoRa-S3** builds come from separate CE trees
  (`~/MeshPocket/RNode_Firmware_CE`, `~/EoRa-S3/RNode_Firmware_CE`) and
  still show the stock strip until the two arrays are ported there.
