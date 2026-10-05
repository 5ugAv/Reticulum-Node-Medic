# Handover — read this first

Rewritten 2026-10-04 against commit `edd7e950`. The previous version was
written 2026-08-24/25 and had rotted: it pointed the deploy recipe at a file
that is not in the repository, described a boot server left running one night
in August, and quoted a test count from six weeks earlier. It is in git history
(`git log -- docs/HANDOVER_NEXT_SESSION.md`) if you need the August narrative.

Start here, then:

- **`README.md`** — the public front page; what the medic does today, computed
  counts and the commands that computed them, known gaps.
- **`docs/BUILD_A_MEDIC.md`** — blank Pi 5 to running medic, with the holes
  named.
- **`docs/HANDOVER.md`** — the durable reference: architecture, the firmware
  contracts, the testing model. Do not overwrite it.
- **`docs/WORKING_METHOD.md`** — the paid-for working rules and the previous
  assistant's own failures. Read before writing code.
- **`docs/READINESS_LEDGER.md`** — every known fault, with status; the thing to
  work from.
- **`docs/V1_SCOPE.md`** — what version 1 is, and the rule that new ideas get
  written down, not built, until medic #2 is in someone's hands.

Your auto-memory `MEMORY.md`, if you have one, is the deep index of verified
facts. Trust it; it was battle-tested.

---

## 1. What Node Medic is FOR (unchanged)

A Raspberry Pi 5 touchscreen tool that **builds, monitors, diagnoses, repairs
and clones Reticulum LoRa mesh nodes**, and now also **messages** over the mesh.
Carried, not installed. Founding principle: *nodes stay useful and repairable
when their keeper moves away or dies.* It must WORK with no internet — carry, do
not fetch. Nothing on any screen may say something unverified: **true = what the
thing reported NOW** (this rule is in `docs/history/SPEC.md` and `docs/WORKING_METHOD.md`).

Vocabulary that must hold in anything a user reads: the self-copy feature is
**Clone** (the code keeps `mitosis` as an identifier); the messenger is
**Chat**; the message relay is a **propagation node** — the words "post office"
were removed from the UI in `a199a7d4`.

---

## 2. State of the project (2026-10-04)

**The release criterion** (`docs/V1_SCOPE.md`): one medic, cloned, handed to a
builder who did not make it. Of the five things that must be flawless first,
three are ticked — Birth, Monitoring, Chat (first live two-way exchange over
LoRa with a phone, 2026-10-01). Maps and the Antenna guide still need a person
at the bench. The front page a stranger can read is done and pinned by tests.

**The readiness ledger** (`docs/READINESS_LEDGER.md`) is the honest map. A
2026-10-03 sweep raised 214 findings; on 2026-10-04 every one was re-verified
against the code. Its header line carries the live count (read it, do not
quote this file); on the evening of 2026-10-04 one **blocker** was left:

- `#174` — the setup walkthrough verified a recovery key and passphrase and
  then threw them away; its summary said they would open the vault. The
  keeper chose DROP for v1 (2026-10-05): the walkthrough is the tour alone,
  the lock steps stay in `ui/setup_flow.py` behind `SECURITY_HALF = False`
  for Node Medic 2, and Settings ▸ Encrypt my records is the one real path.
- `#115` (the clone's kiosk `.deb`s) was closed the same evening: a preflight
  before the card is erased, a Field-readiness top-up, and `--reinstall` in
  the deb fetch.

The sections of the ledger are, in order: *Blocks the clone test*, *Blocks
going public*, *A first-time user meets it*, *Everything else*. Work them in
that order unless the operator says otherwise. The ten README / handover items
in *Blocks going public* were closed by the documentation pass this file is
part of.

**What the last thirty commits did** (read `git log --oneline -30`; the
headline items):

- The readiness sweep landed in batches: three blockers (a crash, a fake
  signal, the wrong image), four more (a diagnosis that lied, a delete that
  spread, a clone that could not finish), then batches A and B of confirmed
  majors, then "nine small ledger items" and "the eight items in the way of
  the clone test".
- **RNode firmware pinned at 1.86**; Field readiness reports a newer upstream
  release without fetching it; "post office" removed from the UI; the chat
  says a message is "held here until they're back online".
- **PROBE** reads an RTNode-2400 the way its birth was verified (reset, then
  listen — it never speaks KISS on USB); its header follows the USB bus while
  the page is open; the Run button names the board; the control socket gained
  a `probe` verb.
- **Chat**: the propagation-node fallback is sent from its own thread; the
  chat names the node holding a waiting message; Ping asks the address the
  node speaks on.
- **Privacy**: no real fleet addresses in fixtures, help text or comments.
- The V4 image BUILD flashes is rebuilt from the fork and the tool knows when
  it is stale; a rebirthed RNode keeps its hash-tail name; the RNode face
  carries a NODE MEDIC strip.

**Deployed versus committed.** This file cannot know what is running on the
live medic. Deploy is an rsync, so `git log` **on the medic** describes the
last commit it happened to have, not the code on disk. Check with
`git status --porcelain` there, or grep the medic's tree for the code you care
about. Assume nothing committed after the operator's last deploy is live until
you have looked.

---

## 3. Deploy + verify mechanics (memorise)

- **Update on the medic**: `cd ~/reticulum-tool && git pull && bash scripts/restart_ui.sh`.
- **From a laptop**: copy exactly the tracked set, never a subset — a partial
  rsync once took the UI down (a module was stale against its caller). For
  example: `rsync -a --files-from=<(git ls-files) . nodemedic@<medic-hostname>.local:~/reticulum-tool/`
  then restart as above. The `.git/hooks/` airlock scripts (`pre-commit`,
  `airlock-check.sh`, `deploy-medic.sh`) that the old recipe named are
  **local to one checkout and not tracked** — a fresh clone has none of them.
  If the project wants them, they have to be committed under `scripts/`.
- **Restart** = `ssh nodemedic@<medic-hostname>.local 'cd ~/reticulum-tool && bash scripts/restart_ui.sh'`.
  It **refuses** (exit 3, with the reason) while an SD image write, a board
  flash, an `rnodeconf` run, an nRF52 DFU or an arduino-cli upload is in
  progress, or while the UI's own busy marker (`~/.reticulum-node-medic/ui_busy`,
  fresh within 90 s) says the UI is working — `scripts/ui_busy_guard.sh`.
  `FORCE=1` overrides, only when the running instance is the thing that is
  broken; `STOP_ONLY=1` stops without starting. Never restart a busy medic.
- **Scrambled panel colours**: Settings ▸ Display ▸ *Fix screen colours*
  (`provisioning/screen_fix.py`, also runnable from SSH — see README ▸ *If
  something goes wrong*).
- **Logs**: `~/ui.log` (every launch road appends, unbuffered — but silence
  still proves nothing), `~/ui_crash.log` (faulthandler), `journalctl -u rnsd`
  / `rnode-splitter` / `lxmd`.
- **Drive the live app** from a shell: `python3 scripts/medic_control.py
  list | open <screen> | node <hash-prefix> | home | probe` (`ui/remote.py`).
- **Registry**: `~/.reticulum-node-medic/registry.json`, saved by the running
  UI about every ten poll cycles (~5 min). Stop the UI (`STOP_ONLY=1`) before
  editing it by hand, or the autosave clobbers your edit. Load in code via
  `NodeRegistry.load(path)`.
- **Test gate**: `python3 -m pytest` before any deploy. `pytest.ini` already
  passes `-q`; use `python3 -m pytest -o addopts= -q` when you want the
  summary line (6735 collected on 2026-10-04). CI runs the suite on every push
  on Python 3.11 and 3.12 (`.github/workflows/ci.yml`). The count only goes up.
- **The medic's sudo is scoped** (`provisioning/sudoers.d/nodemedic`, applied by
  `provisioning/security/apply_sudoers.sh`). A privileged command that is not
  in that file fails by design; a new one needs the operator to re-apply, and
  the live sudoers can lag the repo. You cannot shut the medic down remotely;
  ask the operator.

---

## 4. Method — the rules the operator pays for

- **Two agents, different lenses** on everything nontrivial; commit AND PUSH
  before launching (agent worktrees branch from origin/main). File-editing
  agents: `isolation: worktree`. Never `git add -A` while one runs.
- **Keep moving until clean**: finish → test → commit → deploy → verify
  without asking. But risky/destructive steps are talked through, and
  hard-to-reverse actions get an explicit question with a recommendation first.
- **Evidence before hardware**; hardware gets ONE attempt. **Verify through the
  medic's own functions** (PROBE / BUILD / VITALS / the control socket), not
  ad-hoc SSH scripts.
- **Say only what you checked**, date the evidence, "couldn't check" is an
  answer. Counts are computed, not remembered. When a premise shifts mid-task,
  STOP and put the changed decision back to the operator.
- **Bench instructions** are bare dot-point actions, one per line, name first;
  terminal commands are self-contained for a brand-new window; name the
  success line.
- **Privacy before anything leaves the machine**: no real node or LXMF hashes,
  no email addresses, no Wi-Fi names, no fine coordinates, no home directories
  with a person's name in them, in docs, fixtures or commit metadata.
- **Nothing new gets built before medic #2 is handed over** (`docs/V1_SCOPE.md`).
  Ideas go on the list there.

---

## 5. Open decisions parked with the operator

Carried forward from the August handover; none has been closed in the code
since, as far as this rewrite could see. Re-confirm with the operator before
acting on any of them.

- Boot order baked into clones: `RECOVERY_BOOT_ORDER = "0xf321"` (network boot
  on, so a sealed clone can be rescued over ethernet, but unsigned) versus
  `"0xf31"` (signature-only). Flagged in `workflows/clone.py`, not decided.
- Manifest/APK signing key; the SSH master key into the vault.
- Foreign-node "adopt these addresses as one node" (offered, liked, not
  commissioned).
- Encrypt-at-rest: the per-file records vault is built and the Settings switch
  exists (`provisioning/encryption_flow.py`); the setup walkthrough is not yet
  wired to it (ledger `#174`, `#139`).
- Permanent SD-reader form factor (blocks finalising the guided "insert the
  card" animation).

---

## 6. Known traps that will bite you first

Read the matching memory note (or the comment at the cited file) before
touching the subsystem:

`rnodeconf-byid-nrf52-trap` (raw `/dev/ttyACMx` only — a by-id symlink can
provision the Pi's own UART), `rnodeconf-autoinstall-confirm-hang`,
`nrf-raw-reflash-hash-trap`, `v4-rgb-flash-size-detect`,
`stuck-white-led-blocks-reflash`, `medic-usb-port-map` (the medic's OWN radio
is guarded by `assert_flashable()` — the tool must never write it),
`card-wifi-is-rfkill-blocked`, `cable-birth-stale-host-key`,
`stale-module-after-rsync`, `mesh-listener-bootrace`,
`path-table-is-not-a-sighting`, `rtnode-usb-serial-is-a-log` (an RTNode-2400
never speaks KISS on USB; PROBE resets it and listens).
