# Handover — read this first

Written 2026-08-24 at the end of a long session, for the next assistant.

Start here, then: **`docs/HANDOVER.md`** (original firmware contracts — do not
overwrite), **`docs/WORKING_METHOD.md`** (the paid-for rules + the previous
assistant's failures; read before writing code), **`docs/NEXT_BRIEFS.md`**
(specified jobs, ordered because they conflict). Your auto-memory MEMORY.md is
the deep index — nearly a hundred verified facts with names like
`[[rnodeconf-byid-nrf52-trap]]`; trust it, it is all battle-tested.

---

## 0a. OVERNIGHT 2026-08-25→26 — HAWKEYE sealed-case recovery + mitosis polish

**The one action waiting for the operator:** HAWKEYE (the 2nd medic) is fully
assembled in its case — NO button/SD/pin access, only power-cycle + HAT-vs-USB-C
switching. Its SD boot partition is blanked and its EEPROM was mis-baked to
`0x71` (SD then HTTP-boot). A boot server is LEFT RUNNING on the medic (dnsmasq
on eth0, bound to the HAWKEYE umbilical) with a watcher that writes
`/tmp/hawkeye-verdict.txt` the instant HAWKEYE next boots. **Morning: power-cycle
HAWKEYE, then `ssh nodemedic@nodemedic.local 'cat /tmp/hawkeye-verdict.txt'`.**
- `VERDICT=TFTP_MODE2` → headless recovery is ON: serve an NFS-root netboot
  (recipe in memory `[[hawkeye-identity-netboot]]` + the netboot agent report),
  get a root shell on HAWKEYE over the cable, rebuild p1 + set BOOT_ORDER=0xf321.
- `VERDICT=HTTP_MODE7` → no headless door (HTTP boot is signed to raspberrypi.com).
  Fallbacks: NAT HAWKEYE to real internet so the genuine installer runs on its
  OWN touchscreen (operator can tap it), or open the case. Both agents + RPi docs
  confirmed this fork; the wire decides.
HAWKEYE hardware IDs + full story: memory `[[hawkeye-identity-netboot]]`.

**Shipped to main tonight (CI green, NOT yet deployed to the live UI):**
- `41a1216` dual-supply tripwire (HAT+USB-C at once → red alarm + safe-shutdown;
  reads the PMIC EXT5V; inert without the HAT).
- `b219c15` RPIBOOT is boot-order nibble 3 not 7 (the HAWKEYE mistake).
- `4a35680` MITOSIS **address-agnostic peer discovery** (stock raspberrypi.local
  + full /29 sweep + `ip neigh` + IPv6 link-local fe80:: — finds a Pi 5 at ANY
  address) AND boot-order bake now **0xf321** (SD→NETWORK→RPIBOOT→loop) so a
  sealed future child is recoverable over ethernet alone — the HAWKEYE lesson.
- `d0395ac` **THE FIRSTBORN** tour step: a fresh medic births its own Heltec
  Tracker (GPS/time source) with celebration; `ui/firstborn_flow.py` (tested) +
  `ui/screens/firstborn_screen.py` + wired dormant in app.py.

Two adversarial review agents reviewed `4a35680`+`d0395ac`; their findings were
vetted and applied in `6a0478e` (discovery neighbour-sweep scoped to the cable
so a shared-LAN stranger is never a clone target; firstborn shows the Tracker
board image + a real-fix-proof celebration + honest failure; the tour's green
button no longer masquerades as the ceremony entrance; PROBE ▸ "Birth the GPS
Tracker" makes the "later from PROBE" promise real; MITOSIS clone-done text
stops calling the firstborn "its own radio"). The critical finding (a failed
flash firing the success celebration) was already fixed in `743b547`. ONE
finding deferred by design: the "ping with no radio → offer the Tracker" prompt
— the operator wanted to co-design that walkthrough.

**Deploy step still owed** (do it WITH the operator — a top-level app.py import
of the new firstborn screen means a bad construction would take the UI down;
verified import-safe under mock kivy, but widget construction only runs on a
real display): rsync to the medic + restart the UI (suite green; CI green).

**Boot-order decision owed:** `RECOVERY_BOOT_ORDER` is 0xf321 (network-boot on,
enables sealed-case recovery but unsigned) — the operator can revert to "0xf31"
for signature-only boot. Flagged, not decided.

---

## 0. STATE AT HANDOVER (2026-08-25, late night) — all landed, nothing mid-flight

Tonight's arc ended CLEAN. Deployed and verified live on the medic (c5d77b8):

- **ELSEWHERE fully reborn** (Pi 3A+ + Heltec V4): ONE green VITALS icon,
  all four chips green (LORA/WIFI/BT/NET), SIG -42 dBm. The one-icon fix
  proven on a real rebirth.
- **Ghost neighbours SOLVED for good** — tombstones now enforced at EVERY
  registry ingest + rebirth auto-retires predecessor hashes from prior
  certificates. Full story + code map in memory `rebirth-ghosts-solved`.
- **BT rides the LoRa beacon** (v2 tail KNOWN/UP bits) — and honestly exposed
  that the image ships Bluetooth rfkill-soft-blocked; birth's ON branch now
  unblocks (memory `card-wifi-is-rfkill-blocked`).
- **DSI panel glitch = one tap** — Settings ▸ Display ▸ "Fix screen colours"
  (memory `display-shift-is-panel-not-code`).
- **Observation<T> migration complete** (memory `observation-refactor-shipped`).
- 3A+ birth wording fixed (both power sockets named).

Caveat for the next birth: the running UI imported workflows/build.py BEFORE
the Bluetooth-ON fix deployed — restart the UI once before the next birth so
configure_bluetooth's new ON branch is the one that runs.

The operator plans more sequential-numbered births; watch each land as ONE
icon. Deploy = rsync (medic git HEAD lies); test cmd
`python3 -m pytest tests/ -o addopts="" -q` (4033 pass).

---

## 1. What Node Medic is FOR (unchanged)

A Raspberry Pi 5 touchscreen field tool that **builds, monitors, diagnoses
and clones Reticulum LoRa mesh nodes**. Carried, not installed. Founding
principle: *nodes stay useful and repairable when their keeper moves away or
dies.* It must WORK with no internet — carry, do not fetch. Nothing on any
screen may say something unverified: **true = what the thing reported NOW**
(this rule is in SPEC.md).

---

## 2. Where the fleet stands RIGHT NOW

- **Registry is EMPTY by choice.** 2026-08-24 the operator chose "wipe
  everything, keep nothing" to clear 14 ghost rows (3× ELSEWHERE, 4×
  SKYFINGER, 7 stale hash-only). Backup:
  `~/.reticulum-node-medic/registry.json.bak-20260824-234226-wipe`.
  `kin.json` already empty. **Wipe method matters**: STOP the UI first
  (shutdown/5-min autosave clobbers a file edit), then backup, then
  `{"nodes": []}`, then start.
- **The operator is rebirthing nodes with sequential numbered names**, one at
  a time, through the touchscreen BIRTH flow. The one-icon-per-node fix is
  deployed: each rebirth must land as exactly ONE VITALS icon (name uniqueness
  enforced at birth via `retire_same_name`, no cross-device union in
  `register_device`, lxmd identity regenerated at birth). Watch each one land
  and confirm.
- Live nodes re-appear on their own as SINGLE rows as they announce (the
  lowercase `elsewhere` propagation node is the live one). Stale nodes stay
  gone.

## 3. What shipped this session (all deployed + verified live)

- **Observation<T> refactor, Stages 0–3 — CLOSED.** `monitor/observation.py`
  Observation(value, observed_at, source); one clock-skew policy
  (`age_at`, tolerance 120 s); five liveness stamps folded (`seen`,
  `last_direct_obs`, `last_echo_at_obs`, `last_heard_announce_at_obs`,
  `mesh_heard_obs`); bare names served by ONE `_EpochView` data descriptor
  (registry.py) that stamps each field's intrinsic honest source on write.
  Serialization writes `_obs` dict + bare-float ROLLBACK MIRROR; loads prefer
  `_obs`, fall back `from_legacy` → source "legacy"; both loaders raise-proof
  (corrupt entry nulls the FIELD, never the fleet). Every stage: 2+
  adversarial review lenses, CI green 3.11+3.12. Deployed `f614208`, live
  registry loaded through it cleanly before the wipe.
  **Honest outcome told to the operator: the predicted line SHRINK did not
  materialize (+80 net); the payoff is safety.** Do not re-litigate.
- **GPS clock discipline** — proven outdoors (20-min-wrong clock corrected;
  `datetime.json` source=GPS). Sudoers re-applied so `timedatectl` works
  (`sudo bash provisioning/security/apply_sudoers.sh` — any NEW scoped
  command needs a re-apply by the operator; live sudoers lags the repo).
- **Away-batch** (merged while operator was away, then deployed on return):
  durable atomic writes (incl. trust-store HMAC folded, rollback-safe),
  GPS/NTP fallback decision (no internet probe — it phoned home in review and
  was removed), PROBE parked-radio warning (freq≈0 + EEPROM-valid), regex
  fixes.
- **Honesty fixes**: replayed byte-identical announce ≠ sighting; path-table
  entry ≠ sighting; medic filtered from its own neighbour list; echo
  annotation; ping delivery honesty (no_route / unanswered / answered are
  three different truths).

## 4. Deploy + verify mechanics (memorize)

- Deploy = `bash .git/hooks/deploy-medic.sh` (rsync; airlock refuses dirty
  trees). **`git log` ON THE MEDIC LIES** — rsync does not move HEAD; check
  `git status --porcelain` there, or grep for the code itself.
- Restart = `ssh … 'cd ~/reticulum-tool && bash scripts/restart_ui.sh'` —
  it REFUSES during flashes/SD writes. Never restart a busy medic.
- UI logs to `~/ui.log` (buffered stdout — silence proves nothing).
  Registry: `~/.reticulum-node-medic/registry.json`. Load in code via
  `NodeRegistry.load(path)` (classmethod).
- Test cmd: `python3 -m pytest tests/ -o addopts="" -q` → **4009 passed,
  11 skipped** as of f614208. CI runs on push (GitHub Actions, 3.11+3.12).
- The medic's sudo NEEDS the operator's password (device hardening is LIVE).
  You cannot shut it down remotely; ask the operator.

## 5. Method — the rules the operator pays for

- **Two agents, different lenses** on everything nontrivial; commit AND PUSH
  before launching (agent worktrees branch from origin/main). File-editing
  agents: `isolation: worktree`. Never `git add -A` while one runs.
- **Keep moving until clean**: finish → test → commit → deploy → verify
  without asking. But risky/destructive steps are talked through, and
  hard-to-reverse actions get an explicit question (AskUserQuestion with a
  recommendation first).
- **Evidence before hardware**; hardware gets ONE attempt. **Verify through
  the medic's own functions** (PROBE/BIRTH/MONITOR), not ad-hoc SSH scripts.
- Bench instructions = bare dot-point actions; terminal commands
  self-contained for a brand-new window; name the success line.
- Say only what you checked, date the evidence, "couldn't check" is an
  answer. When a premise shifts mid-task (as the Observation line-count did),
  STOP and put the changed decision back to the operator.

## 6. Open decisions parked with the operator

- Teal paint mark on the V3×Zero2W crossover chart — meaning unconfirmed.
- Manifest/APK signing key decision; SSH master key into the vault;
  MAC scrub + history rewrite.
- Foreign-node "adopt these addresses as one node" feature (offered, liked,
  not commissioned).
- Encrypt-at-rest vault: BUILT, committed, NOT enabled (boot-unlock UX +
  key management, task #34). vault.py atomic-write migration +
  rootfs_user.py /etc/passwd atomicity deferred.
- Permanent SD reader form-factor (blocks finalizing the guided 'insert SD'
  animation).

## 7. Known traps that will bite you first

Read these memories before touching the matching subsystem:
`rnodeconf-byid-nrf52-trap`, `rnodeconf-autoinstall-confirm-hang`,
`nrf-raw-reflash-hash-trap`, `v4-rgb-flash-size-detect`,
`stuck-white-led-blocks-reflash`, `medic-usb-port-map` (P1=3-2 is Jonesey —
the medic's OWN radio, `assert_flashable()` guards it), `card-wifi-is-
rfkill-blocked`, `cable-birth-stale-host-key`, `stale-module-after-rsync`,
`mesh-listener-bootrace`, `path-table-is-not-a-sighting`.
