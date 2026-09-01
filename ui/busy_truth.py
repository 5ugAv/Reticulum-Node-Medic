"""What the busy screen may truthfully say, per kind of build.

Operator, 2026-08-14: "Everything that's on the screen should be telling the
truth to the user. I don't want anybody misled on any of the processes."

Before this module, one hardcoded paragraph claimed "the firmware compile is
the slow part (a first build also downloads the toolchain)" for EVERY build,
and the don't-power-off banner called every build "Flashing". Both were true
for exactly one kind of work and false for the rest: a Pi provisioning run
compiles nothing (its slow part is installing software onto the Pi), and an
autoinstall flash writes PREBUILT firmware from the cache — a write, not a
compile. Only the arduino-cli boards (Wireless Tracker) and the RTNode-2400
genuinely compile, and only they may say so.

Kivy-free on purpose: the choosing of words is a fact about the build, not
about the widget that shows them, and this way the suite can hold every
sentence to account without a window.
"""


def busy_truth(node_type: str, board, display_name: str):
    """(banner, paragraph) for the busy view — each true for THIS build.

    ``node_type`` is the birth chooser's kind ("pi_rnode", "rnode_flash", or
    an RTNode build key); ``board`` the RNodeBoard involved (None when there
    isn't one); ``display_name`` the node's name where one exists.
    """
    wait = ("Keep everything plugged in and WAIT for the green "
            "'Build finished' confirmation before touching anything.")
    if node_type == "pi_rnode":
        name = display_name or "the node"
        return (f"Building {name} — keep the Pi connected, don't power off",
                "Working… installing the software onto the Pi is the slow "
                "part (Reticulum, LXMF and their dependencies). Nothing is "
                f"compiled. {wait}")
    if node_type == "rnode_flash" and board is not None:
        if board.flash_method != "autoinstall":
            # Anything we build here, whatever pushes the image afterwards
            # (arduino-cli upload, or serial DFU). Tested on flash_method
            # rather than on "arduino_cli" because the sentence below is about
            # whether a COMPILE happens, and serial_dfu compiles too — telling
            # the operator "nothing is compiled here" while the medic sits in a
            # toolchain build is exactly the kind of screen that gets a board
            # unplugged mid-flash.
            # This path REALLY compiles — the old sentence is true here.
            # Two lines: the ACT reads at a glance, the board name cannot
            # shrink it (briefing Task 7).
            return (f"Flashing RNode\n{board.display_name} — keep it "
                    "plugged in, don't power off",
                    "Working… the firmware compile is the slow part (a first "
                    f"build also downloads the toolchain). {wait}")
        return (f"Flashing RNode\n{board.display_name} — keep it plugged "
                "in, don't power off",
                "Working… writing the firmware onto the board is the slow "
                f"part. It is prebuilt — nothing is compiled here. {wait}")
    # RTNode-2400 and kin: arduino-cli builds firmware from source.
    name = display_name or "the node"
    return (f"Building {name} — keep it plugged in, don't power off",
            "Working… the firmware compile is the slow part (a first build "
            f"also downloads the toolchain). {wait}")
