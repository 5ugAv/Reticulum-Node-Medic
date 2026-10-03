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

## How the bytes reach a board — and the trap that cost a birth

A Heltec V4 is NOT flashed from the fork by `make`. BIRTH flashes the
tool's own compiled image, `~/RNode_Firmware/build/rnm-board-0x3F/`
(`workflows/rnode_v4_rgb.py`, the NeoPixel build, provisioned as the vendor
V4). `make firmware-heltec32_v4` writes to arduino-cli's default directory,
which birth never reads — so on 2026-10-03 the strip was committed, built
twice that way, and 5A59 was born with the stock face. Two fixes:

* `scripts/rebuild_rnode_firmware.py` compiles into the right directory and
  PROVES the committed strip bytes are in the result. Run it on the medic
  after any change to the fork.
* The tool's `compile_command()` quoted an unexpanded `~` in `--build-path`
  (bash does not expand it there), so even the tool's recipe built into a
  literal `./~/` directory. Absolute paths now; a test pins it. BIRTH also
  says in its flash step when the image is older than the tree's last commit.

Other official OLED boards (V3, LoRa32, T3-S3, T-Beam) are flashed by
`rnodeconf --autoinstall` from the cached upstream release and still show
the stock strip; giving them the cross means a compiled path per board.

## Seen on glass

2026-10-03, Heltec V4 **5AC3**, born through BIRTH after the rebuild: the
strip reads NODE MEDIC with the cross, RNODE and the hash tail below, the
version card and the instrument panel exactly as upstream. The first
Node-Medic-faced RNode.

## Not yet

* 5A59 (born 19:22 that day, before the rebuild) still wears the stock
  face until it is reflashed.
* Pushed to GitHub (`git push origin medic-cross` from `~/RNode_Firmware`;
  the commit already carries the noreply author).
* The **MeshPocket** and **EoRa-S3** builds come from separate CE trees
  (`~/MeshPocket/RNode_Firmware_CE`, `~/EoRa-S3/RNode_Firmware_CE`) and
  still show the stock strip until the two arrays are ported there.
