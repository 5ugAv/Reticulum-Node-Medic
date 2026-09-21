# Firmware naming — RTNode-2400-NM

Decided 2026-09-21 (operator), after the question "we altered RTNode-2400
slightly — should we be calling it something different?"

**Family:** `rtnode2400`. The machine key in certificates, the coverage
table and the classifier. It says what a T114 and an EoRa-S3 have in
common: they run the RTNode-2400 kind of firmware. Unchanged.

**Name:** **RTNode-2400-NM** — the Node Medic build. Human-readable, with
upstream's exact name kept whole inside ours and "-NM" as the variant
suffix. Written into every certificate the medic issues from now on
(`firmware_name`, `firmware_variant: nm`) and shown on the node's VITALS
page as *Firmware: RTNode-2400-NM 0.7.0+nm.1*. Certificates from before
this date carry no name and show the family, *RTNode-2400*.

**Version:** upstream's version plus semver build metadata, `+nm.N`, N
bumped per NM release: `0.7.0+nm.1`. The binary health beacon carries
only major/minor/patch, so it reports `0.7.0`; the NM mark travels in the
firmware's `/status` JSON `fork` field and in the certificate.

**Why not a new name:** the fork still merges upstream
(GrayHatGuy/RTNode-2400, GPL-3.0); a new project name would hide that
debt. GPL-3 §5a asks that a modified version carry prominent notice that
it was modified, and when — the fork's README now does. Rename outright
only if upstream is no longer merged; then it is a different project.

**Where it lives:** firmware `HealthStatus.h` (`RTNODE_FORK_VERSION`,
`RTNODE_FORK_NAME`), `Display.h` default caption `RTNode-NM`, README
notice; medic `workflows/rtnode_build.py` (certificate),
`provisioning/board_coverage.py` (`BoardFact.name`),
`ui/screens/node_detail_screen.py` (Built by this medic).
