"""The ONE hand-kept list: board × firmware pairings we mean to prove, and
the Pi hosts we mean to prove a Pi+RNode build against.

Everything else in board coverage derives itself from the cert ledger
(provisioning.board_coverage). Edit THIS file to add an intention; never
hand-edit docs/BOARD_COVERAGE.md — it is generated. When a birth proves an
intention, the next regeneration drops it from the todo on its own
(operator, 2026-09-18).

Board display names must match the certificate's ``board`` field exactly
(the RNodeBoard.display_name the medic stamps), or the cross won't match.
"""

#: (board display name, firmware kind) we intend to prove.
#: kind ∈ {"rnode", "rtnode2400"}.
INTENT = [
    # The boundary-walk probe: a detachable, low-power, PINGABLE node for the
    # kit (operator, 2026-09-18). RTNode-2400 is the firmware a probe needs —
    # a bare RNode has no address to ping. The T114 is proven as an RNode
    # (t115, 2026-08-21) and antenna-benched (2026-08-27); this is the one
    # birth between it and being kit-ready.
    ("Heltec Mesh Node T114", "rtnode2400"),
    # Kit-probe alternatives, already proven RTNode — kept so the matrix shows
    # the field the operator is choosing within.
    ("LilyGO T-Echo", "rtnode2400"),
    ("Seeed XIAO ESP32S3 (Wio-SX1262)", "rtnode2400"),
]

#: Raspberry Pi hosts we mean to prove a Pi+RNode build against, with the
#: radio each was proven with (or "" for any). Done-ness reads the same
#: ledger — a cert whose role is a Pi build, born against this host.
PI_HOSTS_INTENT = [
    ("pi_3a_plus", "Heltec LoRa32 v4"),
    ("pi_zero_2w", "Heltec LoRa32 v4"),
]
