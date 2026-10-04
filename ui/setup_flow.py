"""First-use setup — the ordered steps and the rules about their order.

Pure data and pure rules: NO Kivy, so the ceremony can be unit-tested and CI
(which has no Kivy) can import it. ``ui.screens.setup_wizard_screen`` is only
the presentation over this, exactly as ``ui.birth_guide_flow`` is to the guided
birth.

Operator, 2026-08-11: "we need to make sure the process of setting up the
passwords, pattern and usb are all super clear and the user is pointed towards
them at first use of medic. there should be a set up process that implements the
security and walks the user through the different functions of the medic."

THE ORDER IS THE FEATURE, and it is not the order a person would choose. Left to
themselves an operator picks the pattern first: it is the fun one, it is the one
phones taught them, and it is the one that takes ten seconds. Every one of the
lockouts this design exists to prevent starts there. So the ceremony runs
backwards from the way in that cannot be forgotten:

    the recovery key is generated and WRITTEN DOWN,
    then TYPED BACK, because a key seen on a screen is not a key on paper,
    then a passphrase is set,
    and only then may a pattern or a USB key be added on top.

``provisioning.vault_factors.can_select`` owns those rules and this module does
not restate them — it refuses a pattern before a passphrase, and it refuses any
level until the recovery key has been typed BACK rather than merely generated.
What the model still cannot see is the ORDER of the screens, so ``blocked_reason``
carries the step-level gates and nothing advances past them.

WHY THE TOUR IS IN THE SAME FLOW rather than a separate help screen. Help
screens are read by people who already know what they are looking for. This one
runs once, when the medic is new, and its job is to answer the question nobody
asks out loud: what is this thing FOR. One screen per mode, in the order a
medic is actually used — build something, watch it, decide where the next one
goes — and each says what the mode is for rather than listing what it can do.

# i18n: NOT wrapped, deliberately, and this is a decision not an oversight.
# ``ui/screens/settings_screen.py`` already documents the same split: short
# chrome is wrapped, long descriptive copy is not yet. Here the reason is
# sharper. Every catalog under assets/i18n must carry the same key set (the
# parity test enforces it), so wrapping this module means shipping machine
# translations of SECURITY copy into eight languages, none of which anyone here
# can read back. The standing rule is that nothing is stated unless it has been
# checked — and "the medic still relays while locked", rendered into Japanese by
# a tool nobody can proofread, is a claim about someone's fleet made on trust.
# English until a speaker checks it; the strings live here, ready to be wrapped.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import List, Optional

from provisioning.vault_factors import (KEYFILE, LEVELS, PASSPHRASE, PATTERN,
                                        Enrolment, Policy, can_select)

#: The two halves of the walkthrough.
SECURITY = "security"
TOUR = "tour"

#: Step keys. Named constants because three modules (the flow, the screen and
#: the tests) all have to agree on them, and a typo in a bare string is a step
#: that silently never gates.
WELCOME = "welcome"
WHAT_IS_LOCKED = "what_is_locked"
RECOVERY_KEY = "recovery_key"
RECOVERY_KEY_BACK = "recovery_key_back"
PASSPHRASE_STEP = "passphrase"
LEVEL = "level"
PATTERN_STEP = "pattern"
KEYFILE_STEP = "keyfile"
SECURITY_SUMMARY = "security_summary"

TOUR_BIRTH = "tour_birth"
TOUR_FIRSTBORN = "tour_firstborn"
TOUR_VITALS = "tour_vitals"
TOUR_SCAN = "tour_scan"
TOUR_MAPS_DOWNLOAD = "tour_maps_download"
TOUR_TRIAGE = "tour_triage"
TOUR_CHAT = "tour_chat"
TOUR_PROBE = "tour_probe"
TOUR_MITOSIS = "tour_mitosis"
TOUR_SETTINGS = "tour_settings"
FINISH = "finish"


# --------------------------------------------------------------------------- #
# What the wizard offers.
# --------------------------------------------------------------------------- #
#
# ``vault_factors.LEVELS``, straight through. This wizard briefly kept its own
# shorter copy of the ladder: the model's first rung was then pattern-only, the
# operator had ruled that out on 2026-08-11 — "let's go for maximum strength" —
# and this is the screen a person meets once, before they have any basis for
# judging ~20 bits against the rest. The model has since dropped that rung
# itself and now requires a passphrase in every level, so the two lists agree
# and there is no reason to maintain the second one. A menu that has drifted
# from its model is a menu offering something the model will refuse.


@dataclass(frozen=True)
class SetupState:
    """How far through the security ceremony this operator has actually got.

    Frozen, and advanced with ``replace``: the screen holds one of these and the
    gates read it. A mutable state object shared between a screen and its own
    callbacks is how a half-finished pattern gets read as a finished one.
    """

    #: The key has been generated and the three write-it-down confirmations
    #: answered. NOT the same as verified — see the next field, which is the
    #: whole reason this pair is two booleans and not one.
    recovery_key_shown: bool = False
    #: The operator typed it back and it matched. This is the only evidence the
    #: medic can ever have that the key left the screen.
    recovery_key_verified: bool = False
    passphrase_set: bool = False
    #: The level chosen on the chooser. None until they choose.
    level: Optional[Policy] = None
    pattern_set: bool = False
    keyfile_set: bool = False
    #: Set when the operator stepped past the security half. Remembered so the
    #: summary can say what was skipped instead of implying it was done.
    security_skipped: bool = False

    @property
    def enrolment(self) -> Enrolment:
        """This state as the vault model sees it, so ``can_select`` can rule on
        it without the wizard re-implementing the model's own conditions.

        BOTH recovery-key booleans cross this line, and they are not the same
        claim. ``recovery_key_set`` is a statement about a LUKS keyslot — the
        key exists and opens the volume — and that is true the instant it is
        enrolled, whether or not anybody wrote it on paper. ``recovery_key_
        verified`` is the ceremony's evidence that it left the screen. This
        wizard was written when the model held only the first and the typed-it-
        back rule had to live up here; ``vault_factors`` now carries both
        fields and makes the distinction itself, so the rule goes back where
        it belongs and there is one copy of it again.
        """
        return Enrolment(passphrase_set=self.passphrase_set,
                         recovery_key_set=self.recovery_key_shown,
                         recovery_key_verified=self.recovery_key_verified,
                         policy=self.level or Policy())

    @property
    def factors_ready(self) -> bool:
        """Is every factor of the chosen level actually enrolled?

        The gate on the summary. Choosing "pattern + passphrase + USB key" and
        then walking out of the pattern screen used to leave a state that looked
        finished from every angle except the one that mattered.
        """
        if self.level is None:
            return False
        if PATTERN in self.level.ordered and not self.pattern_set:
            return False
        if KEYFILE in self.level.ordered and not self.keyfile_set:
            return False
        if PASSPHRASE in self.level.ordered and not self.passphrase_set:
            return False
        return True


def advance(state: SetupState, **changes) -> SetupState:
    """A new state with *changes* applied — the only way the wizard moves."""
    return replace(state, **changes)


# --------------------------------------------------------------------------- #
# The steps.
# --------------------------------------------------------------------------- #
#
# Each step: key, part, title, body, and optionally hint / warning / next label
# / ``self_advancing`` / ``no_back`` / ``poster_card`` / ``opens``.
#
# ``self_advancing`` means the MEDIC decides when the step is done — the typed
# recovery key matches, the second pattern matches, the USB stick appears. Those
# steps must render no green Next at all. Asked for outright on 2026-08-02 for
# the birth walkthrough and it applies identically here: a button that is
# disabled until the moment it becomes redundant has never once been the thing
# that moved the operator forward, and it reads on every successful run as the
# tool waiting on them.
#
# ``no_back`` is for work that must not be interrupted. It is deliberately used
# NOWHERE in this flow: nothing here writes to a disk, resets a board or spends
# four minutes on a card. Every screen keeps its way out. The key is defined so
# that the day something in here does start doing real work, the flow can say so
# rather than the screen quietly dropping a button.

_SECURITY_STEPS = [
    {"key": WELCOME, "part": SECURITY,
     "title": "Set up this Node Medic",
     "body": "Two things, and then you are done. First how this medic locks its "
             "own records — the recovery key, a passphrase, and how you unlock "
             "it day to day. Then a screen each on what the front page's cards "
             "are for.\n\n"
             "If you stop part-way, the walkthrough starts again from the "
             "beginning — nothing is kept until the summary screen.",
     "next": "Start  →"},

    # THE HONESTY STEP, and it goes BEFORE anything is chosen rather than in a
    # summary at the end. What the vault covers is the thing that decides
    # whether any of this is worth doing, and an operator who learns at the end
    # that the mesh identity stays outside has already spent nine screens on a
    # decision they were not given the facts for.
    #
    # Every sentence here is read off provisioning/vault.py's RECORDS_ROOTS
    # comment, which states the trade the operator chose on 2026-08-02. If that
    # set ever changes, this screen is wrong and the test below catches it.
    {"key": WHAT_IS_LOCKED, "part": SECURITY,
     "title": "What the lock actually covers",
     "body": "Locked: this medic's own records. Where each node you have built "
             "really is, its birth certificate, your notes, the position trail.\n\n"
             "Not locked: the mesh identity itself. That is deliberate — the "
             "medic still boots, rejoins the mesh and relays for it while "
             "locked, and comes back on its own after a power cut.\n\n"
             "So a stolen card shows that this is a Reticulum node, and its mesh "
             "address. It does not show where your fleet is, or who runs it.",
     "next": "Understood  →"},

    {"key": RECOVERY_KEY, "part": SECURITY,
     "title": "The recovery key comes first",
     "body": "Node Medic will generate 32 characters and show them once. That is "
             "the only way back into this medic if you forget everything else, "
             "and nothing kept on this card can reproduce it.\n\n"
             "Have something to write on before you tap.",
     "hint": "Not a photo you will delete, and not a note on the medic itself. "
             "A key taped to the case protects nothing.",
     "next": "Show me the key  →"},

    # THE STEP THIS WHOLE FLOW IS BUILT AROUND.
    #
    # The recovery-key ceremony asks three times whether it has been written
    # down, each more pointedly than the last, and an operator can tap Yes to
    # all three while looking at a screen they will never see again. From the
    # medic's side, "shown" and "written down" are indistinguishable. Typing it
    # back is the only evidence that can exist — and the only moment a
    # transcription slip (a 5 read as an S, a group skipped) is still cheap.
    {"key": RECOVERY_KEY_BACK, "part": SECURITY,
     "title": "Now type it back",
     "body": "Type the key from what you just wrote down — not from memory. "
             "Node Medic checks what is on your paper, not what was on its "
             "screen, and this is the last moment a slip of the pen can be "
             "caught.",
     "hint": "Hyphens, spaces and capitals do not matter. A handwritten I or L "
             "is read as 1, and O as 0, so the confusable letters cannot cost "
             "you the medic.",
     "self_advancing": True},

    {"key": PASSPHRASE_STEP, "part": SECURITY,
     "title": "Set a passphrase",
     "body": "Every level of protection below asks for this passphrase, and it "
             "is also enrolled on its own so a forgotten pattern or a lost USB "
             "stick can never shut you out.\n\n"
             "That makes it part of the strongest option AND the easiest door "
             "into this vault. It has to be a real passphrase, not a word.",
     "hint": "You will type it twice. Something you could still enter in a "
             "year, in the dark, on this touchscreen.",
     # A BUTTON, not a self-advance. The two fields matching is not a signal
     # that the operator has finished — it is a signal that they have finished
     # SO FAR, and a screen that jumps away the instant the second field agrees
     # takes the passphrase out of their hands mid-thought, with the keypad
     # still up over the place it went.
     "next": "Set it  →"},

    {"key": LEVEL, "part": SECURITY,
     "title": "How you will unlock it day to day",
     "body": "Pick one. What each asks for, and what it is honestly worth, is "
             "written on the option itself — including what it does not protect "
             "you from. The stronger ones are further down.",
     "self_advancing": True},

    {"key": PATTERN_STEP, "part": SECURITY,
     "title": "Draw your pattern",
     "body": "Draw it once, then draw it again to confirm. Node Medic will not "
             "accept it unless the two match — a pattern is drawn, not read "
             "back, so there is nothing on screen afterwards to check it "
             "against.",
     "hint": "At least four dots, and not a plain L or Z. A pattern leaves a "
             "smudge on the glass: wipe the screen when you are done.",
     "self_advancing": True},

    {"key": KEYFILE_STEP, "part": SECURITY,
     "title": "Put the USB key in",
     "body": "Plug a USB stick into Node Medic. It writes one file onto it — "
             "nodemedic.key — and from then on the vault will not open without "
             "that stick, even with the pattern and the passphrase.",
     "warning": "Make a second stick before you walk away with this one. The "
                "passphrase is what gets you back in if it is lost, so it is "
                "carrying the whole vault on its own the moment the stick goes.",
     # SEEING A STICK MUST NOT WRITE TO IT. The medic notices the stick by
     # itself and says which mount point it found — that part is automatic and
     # has to be, or the operator is left pressing a button to ask whether their
     # hardware is plugged in. The WRITE is a deliberate press, because this is
     # the operator's own USB stick and the medic has no idea what else is on
     # it. Same line the guided birth draws around the SD card: detect
     # automatically, destroy only on request.
     "next": "Write the key to this stick  →"},

    {"key": SECURITY_SUMMARY, "part": SECURITY,
     "title": "What is set, and what is not",
     "body": "",            # written from the live state, never asserted here
     "next": "Show me the medic  →"},
]

# --------------------------------------------------------------------------- #
# The tour: what each mode is FOR.
# --------------------------------------------------------------------------- #
#
# ORDERED THE WAY A MEDIC IS USED, not the way the front page is laid out. You
# build something, you watch whether it lived, you decide where the next one
# goes, you go and aim it, you fix it when it stops. The poster's card order is
# a picture-composition decision and reading a tour in it teaches nothing.
#
# ``poster_card`` names a zone in ui.home_zones so the screen can show the
# operator the ACTUAL painted card they are going to press, cropped out of the
# front page. Standing rule, [[show-dont-tell-ux]]: a picture of the thing in
# front of them beats a sentence describing it. PROBE, MITOSIS and Settings have
# no painted card, so they carry none rather than borrowing someone else's.

_TOUR_STEPS = [
    {"key": TOUR_BIRTH, "part": TOUR, "poster_card": "birth", "opens": "birth_guide",
     # Titles open with the POSTER's word (POSTER_WORD_FOR, repainted
     # 2026-09-13 per docs/FRONT_PAGE_BRIEF.md) — the screen shows the actual
     # painted card beside them, so any other word would contradict the crop.
     "title": "BUILD — turn a board into a node",
     "body": "Plug a bare board into Node Medic and it walks you through turning "
             "it into a node on your mesh: flashed, named, given a birth "
             "certificate, and placed on the map.\n\n"
             "A radio for a phone or laptop, a transport node that runs the mesh "
             "on its own, or a Raspberry Pi and radio together. It tells you "
             "which cable, which socket, and which board you are holding."},

    {"key": TOUR_FIRSTBORN, "part": TOUR, "board_image": "heltec_wireless_tracker",
     "opens": "firstborn", "optional": True,
     "opens_label": "Meet the firstborn  →",
     "title": "THE FIRSTBORN — this medic's own eyes",
     "body": "A Raspberry Pi can't see the sky, so this medic has no position "
             "or clock of its own. Its first child fixes that: a Heltec "
             "Wireless Tracker, flashed here and adopted as the medic's GPS and "
             "time. Every node you place gets a real location and date from it.",
     "hint": "Plug in ONLY the Tracker — the medic's own radio looks alike on "
             "USB and is told apart by its satellite stream, not the socket. No "
             "Tracker on hand? Skip for now — run 'Set up this Node Medic' "
             "again from Settings when you have it.",
     "next": "Skip for now  →"},

    {"key": TOUR_VITALS, "part": TOUR, "poster_card": "vitals", "opens": "vitals",
     "title": "VITALS — is the fleet alive",
     "body": "Every node you have built, and when each was last heard from. "
             "Battery, temperature, what it is carrying.\n\n"
             "Nodes go quiet for ordinary reasons — a cloudy week on a solar "
             "node is not a fault. VITALS is where you find out which ones have "
             "been quiet longer than that."},

    {"key": TOUR_SCAN, "part": TOUR, "poster_card": "scan", "opens": "scan",
     "title": "MAPS — where the next node goes",
     "body": "The map. Your nodes where they actually stand, the links the medic "
             "has heard between them, and the gaps where the mesh does not "
             "reach.\n\n"
             "It is also the front door to placing one: pick a spot, and the "
             "build starts from there with the position already stamped in."},

    # THE MAPS LIVE ON THE MEDIC, AND SOMEBODY HAS TO PUT THEM THERE (operator,
    # 2026-09-29: "if someone's building a Node Medic from the GitHub repo,
    # they'll need to download all the resources — that should happen during
    # setup, not on the screen"). A clone arrives carrying its builder's area;
    # a fresh build carries nothing. Either way this is the moment to say so.
    # The body's {summary} is filled in by setup_steps() from the SQLite the
    # map actually draws from, so the sentence can never disagree with the map.
    {"key": TOUR_MAPS_DOWNLOAD, "part": TOUR, "poster_card": "scan", "opens": "scan",
     "opens_label": "Open MAPS to download an area",
     "title": "MAPS — carried, not fetched",
     "body": "Node Medic keeps its maps on the SD card so the mesh can be built "
             "with no internet at all.\n\n"
             "{summary}\n\n"
             "To add an area you need Wi-Fi once: open MAPS, look at the place, "
             "and the Offline maps control appears — it only shows itself when "
             "the map is looking at somewhere this medic does not carry. "
             "Terrain for that area downloads in the same pass, so the map can "
             "tell you whether two nodes can see each other."},

    {"key": TOUR_TRIAGE, "part": TOUR, "poster_card": "triage", "opens": "triage",
     "title": "ANTENNA — aim it on site",
     "body": "For when you are standing at the node with it in your hands. "
             "Signal, noise and who can hear you, live, as you move the "
             "antenna.\n\n"
             "A node in the right place with a badly aimed antenna and a node in "
             "the wrong place look the same in VITALS. This is how you tell them "
             "apart, on site.\n\n"
             # ONE SENTENCE, NOT A NINTH CARD (2026-09-21). The tour is one
             # screen per mode and a boundary walk is a thing two modes DO,
             # not a mode; and a stranger on this screen owns no nodes yet, so
             # a card about measuring their reach would teach nothing it could
             # use. But ANTENNA carries the button, so the card that owns the
             # button names it, and the "?" guide carries the explanation.
             "It is also where a BOUNDARY TEST starts — you walk away from a "
             "node on foot and the medic tells you where its signal stops. "
             "The \"?\" in the corner explains that one."},

    # The fifth painted card. The tour never mentioned it, and it is the one
    # a stranger taps first (readiness ledger #151).
    {"key": TOUR_CHAT, "part": TOUR, "poster_card": "chat", "opens": "chat",
     "title": "CHAT — talk over the mesh",
     "body": "The medic's own messenger: type a message, send it over the mesh, "
             "read replies.\n\n"
             "A message with no path right now is held by this medic's "
             "propagation node until the other side is back online — in Home "
             "mode. In Backpack mode nothing holds it, and the screen says so.\n\n"
             "The same screen hands a phone the Columba or Sideband app over "
             "Wi-Fi, for messaging from your pocket."},

    {"key": TOUR_PROBE, "part": TOUR, "opens": "probe",
     "title": "PROBE — find out what is wrong",
     "body": "Plug in a board that is misbehaving and the medic reads it: what "
             "it is, what firmware it carries, whether it answers at all.\n\n"
             "Self Diagnose (in Settings, and on this screen) turns the same "
             "attention on the medic itself — its own radio, its GPS, its "
             "clock, its services — and repairs what it can. Run it when the "
             "medic is the thing behaving oddly.",
     "hint": "Self Diagnose is behind the gear: Settings. It is the first "
             "thing to try before suspecting a node."},

    {"key": TOUR_MITOSIS, "part": TOUR, "opens": "mitosis",
     "title": "CLONE — make another medic",
     "body": "Copies this Node Medic onto a fresh Raspberry Pi 5, so a second "
             "person can build and repair nodes without you.\n\n"
             "That is the point of the whole tool. A mesh that only one person "
             "can maintain lasts exactly as long as that person stays.",
     "hint": "The clone gets its own identity and its own vault. Trust between "
             "medics is set up separately, in Settings ▸ Trusted operators."},

    {"key": TOUR_SETTINGS, "part": TOUR, "opens": "settings",
     "title": "Settings — the medic's own dials",
     "body": "The gear at the top right. Language, screen brightness, the radio "
             "parameters every new node is born with, Wi-Fi, and whether this "
             "medic acts as a full mesh node at home or stays quiet in a "
             "backpack.\n\n"
             "This walkthrough lives there too, so you can run it again — or "
             "hand the medic on and let the next person run it from nothing."},

    {"key": FINISH, "part": TOUR,
     "title": "That is the medic",
     "body": "Nothing here is finished by reading about it. Plug a board in and "
             "press BUILD — the walkthrough will not let you damage anything, "
             "and it says what it is about to do before it does it.",
     "next": "Take me to the front page  →"},
]


def setup_steps(state: Optional[SetupState] = None,
                include_security: bool = True) -> List[dict]:
    """The ordered steps for this operator, as copies.

    The list DEPENDS ON STATE, and it has to. The pattern and USB-key steps
    exist only for levels that use them, and which level that is is not known
    until step six. Two ways to handle that and both are ugly:

    * fix the list at the maximum and show a pattern step to somebody who chose
      passphrase-only — a screen that promises work it will not ask for;
    * let the total shrink at the moment the choice is made.

    The second is chosen because the shrink happens ON the screen that caused
    it, immediately after a deliberate tap, which is the only place a moving
    progress counter is explicable. Showing a step that never comes is not.
    """
    st = state or SetupState()
    steps: List[dict] = []
    if include_security:
        for s in _SECURITY_STEPS:
            if s["key"] == PATTERN_STEP and not _level_wants(st, PATTERN):
                continue
            if s["key"] == KEYFILE_STEP and not _level_wants(st, KEYFILE):
                continue
            steps.append(dict(s))
    steps.extend(dict(s) for s in _TOUR_STEPS)
    for st_ in steps:
        if st_["key"] == TOUR_MAPS_DOWNLOAD:
            st_["body"] = st_["body"].format(summary=maps_summary_sentence())
    return steps


def maps_summary_sentence(summary: Optional[dict] = None) -> str:
    """One honest sentence about what this medic carries.

    Read from the map's own SQLite (ui.map_download.carried_summary) unless a
    summary is handed in for a test. Three cases, and the words differ because
    the situations do: nothing (a fresh build — the download is the next
    thing to do), a basemap without terrain, or both.
    """
    if summary is None:
        try:
            from ui.map_download import carried_summary
            summary = carried_summary()
        except Exception:                                          # noqa: BLE001
            summary = {"tiles": 0, "zmin": None, "zmax": None, "terrain": False}
    n = int(summary.get("tiles") or 0)
    if n <= 0:
        return ("This medic carries NO maps yet, so the map will be blank until "
                "an area is downloaded.")
    zr = ""
    if summary.get("zmin") is not None and summary.get("zmax") is not None:
        zr = f" (zoom {summary['zmin']}–{summary['zmax']})"
    if summary.get("terrain"):
        return (f"This medic already carries an area — {n:,} map tiles{zr}, "
                "with terrain.")
    return (f"This medic already carries an area — {n:,} map tiles{zr} — but "
            "no terrain for it yet.")


def _level_wants(state: SetupState, factor: str) -> bool:
    """Does the chosen level use *factor*? Before a level is chosen, YES.

    Fail-open, on purpose. An unchosen level means the answer is unknown, and
    the cost of the two mistakes is not symmetric: an extra step in the counter
    is noise, while dropping the pattern step for someone who is about to choose
    a pattern level would leave the flow with no way to set the thing it just
    made them pick.
    """
    if state.level is None:
        return True
    return factor in state.level.ordered


def step_for(key: str, state: Optional[SetupState] = None) -> Optional[dict]:
    """One step by key, or None. A copy, so a screen cannot edit the source."""
    for s in setup_steps(state):
        if s["key"] == key:
            return s
    return None


# --------------------------------------------------------------------------- #
# The gates.
# --------------------------------------------------------------------------- #

def blocked_reason(key: str, state: SetupState) -> str:
    """Why this step cannot be entered yet, or "" if it can.

    THE GATE IS ON THE STEP, NOT ON THE BUTTON. Disabling Next was the first
    design and it is the wrong one: a wizard has more ways forward than its own
    button — a resume after a hand-off, a Back followed by two Nexts, a screen
    that advances itself. Every one of those bypasses a check that lives on a
    widget. Asking the step whether it may be entered covers all of them,
    including the routes added later.
    """
    if key == RECOVERY_KEY_BACK and not state.recovery_key_shown:
        return ("Node Medic has not shown you a recovery key yet.")
    if key == PASSPHRASE_STEP and not state.recovery_key_verified:
        return ("Type the recovery key back first. Until you do, the only thing "
                "the medic knows is that it printed one — not that you have "
                "it.")
    if key == LEVEL and not state.passphrase_set:
        return ("Set a passphrase first. Whatever daily unlock you pick, "
                "the passphrase stays enrolled as the way back in behind it.")
    if key == PATTERN_STEP:
        if state.level is None or PATTERN not in state.level.ordered:
            return "This medic's chosen level does not use a pattern."
        ok, why = can_choose(state.level, state)
        if not ok:
            return why
    if key == KEYFILE_STEP:
        if state.level is None or KEYFILE not in state.level.ordered:
            return "This medic's chosen level does not use a USB key."
        # PATTERN BEFORE STICK, when both are wanted. Not arbitrary: the stick
        # step is the one that writes to hardware the operator brought, and it
        # is the only step that can fail for a reason outside the medic (no
        # stick, unwritable stick, wrong stick). Everything that can be settled
        # on the glass gets settled first, so a failure there is the ONLY thing
        # outstanding rather than one of three.
        if PATTERN in state.level.ordered and not state.pattern_set:
            return "Draw your pattern first."
    if key == SECURITY_SUMMARY and not (state.security_skipped
                                        or state.factors_ready):
        return ("Some of what you chose has not been set yet.")
    return ""


def can_choose(policy: Policy, state: SetupState) -> tuple:
    """(ok, reason) — may this level be selected right now?

    Pure deference now. Every rule this used to add is one the model owns: a
    passphrase in every level, a passphrase before a pattern or a USB key, and
    WRITTEN DOWN rather than merely shown. That last one was added here because
    the model was handed a generated key and a written-down key as the same
    boolean; it has since grown ``Enrolment.recovery_key_verified`` and refuses
    on it first. Restating it here would be the second copy this wizard's own
    tests exist to prevent — and the copy is the one that stops being updated.
    """
    return can_select(policy, state.enrolment)


def offered_levels(state: SetupState) -> List[tuple]:
    """``(policy, selectable, reason)`` for each level the wizard offers.

    Every level is LISTED whatever the state, with its own reason when it cannot
    be taken. Hiding the ones that are not yet available would leave a chooser
    that silently grows as the operator works, and an operator who never learns
    that a stronger option existed.
    """
    return [(p,) + can_choose(p, state) for p in LEVELS]


def first_incomplete(state: SetupState) -> str:
    """Where to resume: the first step that is not done and is not blocked.

    A medic that is closed mid-ceremony and reopened must land the operator
    where they stopped, and never on a screen whose precondition has gone. This
    returns a step that ``blocked_reason`` will accept, so the resume cannot
    drop anyone onto a gate.

    THE TALKING STEPS ARE SKIPPED ONCE ANY WORK HAS BEEN DONE. Welcome and
    what-is-locked record nothing, so they are never "done" and a naive scan
    returns the first of them forever — an operator who set a passphrase and
    chose a level would be dropped back on the title screen, which reads as the
    medic having lost their work. They are re-shown only to somebody who has not
    yet done anything, where re-reading them is the point.
    """
    steps = [s for s in setup_steps(state) if s["part"] == SECURITY]
    last_done = -1
    for i, s in enumerate(steps):
        if _step_done(s["key"], state):
            last_done = i
    for i, s in enumerate(steps):
        if i < last_done:
            continue
        if blocked_reason(s["key"], state) or _step_done(s["key"], state):
            continue
        return s["key"]
    return TOUR_BIRTH


def _step_done(key: str, state: SetupState) -> bool:
    """Has this step's work been done? Only the steps that HAVE work.

    The talking steps (welcome, what-is-locked) are never "done" — nothing is
    recorded when they are read — so resume lands on the first of them every
    time, which is right: two screens of re-reading is cheap, and the operator
    who stopped there stopped because they had not taken it in.
    """
    return {
        RECOVERY_KEY: state.recovery_key_shown,
        RECOVERY_KEY_BACK: state.recovery_key_verified,
        PASSPHRASE_STEP: state.passphrase_set,
        LEVEL: state.level is not None,
        PATTERN_STEP: state.pattern_set,
        KEYFILE_STEP: state.keyfile_set,
        SECURITY_SUMMARY: False,
    }.get(key, False)


# --------------------------------------------------------------------------- #
# What the summary is allowed to say.
# --------------------------------------------------------------------------- #

def summary_lines(state: SetupState, vault_exists: bool) -> List[tuple]:
    """``(ok, sentence)`` pairs for the summary screen — facts, not reassurance.

    *vault_exists* is passed in from whoever asked the disk, never assumed here.
    The standing rule is that nothing appears on screen unless it is true NOW,
    and "true" means what the thing reported when asked. So this function is
    given the answer rather than inventing one, and every sentence it returns
    names a specific thing that was or was not done.

    NO SENTENCE HERE SAYS THE RECORDS ARE ENCRYPTED unless the caller says a
    vault exists. A summary that congratulated the operator on their protected
    records would be the tool lying about its own state on the screen designed
    to explain that state.

    2026-09-03: the encryption is now BUILT and runnable —
    ``provisioning.records_vault``, per-file AES-256-GCM with a scrypt-wrapped
    data key. It replaced the LUKS container, which could never have shipped:
    ``cryptsetup`` is not installed on the medic and is not in assets/packages,
    so an offline clone had no way to enable it. The copy below no longer says
    encryption is "still being built", because it is not.
    """
    out: List[tuple] = []
    if not vault_exists:
        # LEAD with the state that matters, not bury it under green ticks: a
        # skimmer reads a stack of ✓ lines, feels done, and misses that nothing
        # is actually locked yet (walkthrough 2026-08-26). The factors below are
        # ready-and-waiting, not active protection.
        out.append((False,
                    "NOT LOCKED YET — your records are NOT encrypted. What you "
                    "set below is ready and waiting, but it protects nothing "
                    "until you switch encryption on in Settings. When you do, "
                    "every file in your records is encrypted in place, and each "
                    "of the keys you set today opens it on its own."))
    out.append((state.recovery_key_verified,
                "Recovery key written down and typed back correctly."
                if state.recovery_key_verified else
                "Recovery key NOT confirmed — nothing was verified as written "
                "down."))
    out.append((state.passphrase_set,
                "Passphrase set." if state.passphrase_set
                else "No passphrase set."))
    if state.level is not None:
        from provisioning.vault_factors import level_name
        out.append((True, f"Daily unlock: {level_name(state.level)}."))
        if PATTERN in state.level.ordered:
            out.append((state.pattern_set,
                        "Pattern drawn twice and matched."
                        if state.pattern_set else "Pattern NOT set."))
        if KEYFILE in state.level.ordered:
            out.append((state.keyfile_set,
                        "USB key written and read back."
                        if state.keyfile_set else "USB key NOT written."))
    else:
        out.append((False, "No unlock method chosen."))

    if vault_exists:
        out.append((True,
                    "Your records on this card are encrypted. Your daily unlock, "
                    "your passphrase and your recovery key each open them."))
    # When there is no vault, the honest NOT-LOCKED headline is already at the
    # TOP of this list (see above) — it leads instead of trailing so it can't be
    # skimmed past under the green ticks.
    return out


#: Words a setup screen may never put in front of an operator. Comfort language
#: about security is the exact thing the standing rule forbids: it states a
#: conclusion ("safe", "secure", "protected") where the tool can only honestly
#: state a fact ("the pattern matched", "no container exists"). A test walks
#: every string in this module against this list, because copy like this arrives
#: one well-meaning edit at a time.
FORBIDDEN_CLAIMS = (
    "your data is safe",
    "completely secure",
    "fully encrypted",
    "military grade",
    "unbreakable",
    "cannot be hacked",
    "100% secure",
    "guaranteed",
)


def prose(state: Optional[SetupState] = None) -> str:
    """Every word this flow can put on a screen, joined — for the guard test."""
    parts = []
    for s in setup_steps(state):
        for field in ("title", "body", "hint", "warning", "next"):
            if s.get(field):
                parts.append(s[field])
    for _ok, line in summary_lines(SetupState(), vault_exists=False):
        parts.append(line)
    for _ok, line in summary_lines(
            SetupState(recovery_key_shown=True, recovery_key_verified=True,
                       passphrase_set=True, pattern_set=True, keyfile_set=True,
                       level=LEVELS[-1]), vault_exists=True):
        parts.append(line)
    return "\n".join(parts)
