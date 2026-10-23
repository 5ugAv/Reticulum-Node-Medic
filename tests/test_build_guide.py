"""The public build guide must let a stranger go from zero to a medic (audit
2026-10-23: no parts list existed in the repository, and the things most
likely to wreck a build were unwritten). These pin the builder-facing page
and the README's front-page block to that standard."""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def test_the_guide_opens_builder_first_with_a_parts_list():
    g = _src("docs/BUILD_A_MEDIC.md")
    heads = re.findall(r"(?m)^## (.+)$", g)
    assert heads[:3] == ["What you are building", "Parts list — one medic", "Before you start"]
    assert g.index("## Parts list") < g.index("## Appendix")
    for part in ("Raspberry Pi 5", "Active Cooler", "Touch Display 2", "64 GB",
                 "5 V / 5 A", "Heltec Wireless Tracker", "USB-A to USB-C",
                 "card reader", "Raspberry Pi Imager", "Cults 3D", "UPS Module 3S",
                 "Ethernet cable", "microsoldering"):
        assert part in g.split("## Before you start")[0], part


def test_the_guide_names_the_hazards_a_builder_cannot_guess():
    g = _src("docs/BUILD_A_MEDIC.md")
    assert "socket marked\n  **LoRa**" in g or "socket marked **LoRa**" in g
    assert "ceramic" in g and "works as the\n  board comes" in g or "ceramic" in g
    assert "Not a 3 A one" in g
    assert "Press nothing on the board" in g
    assert "tmux" in g and "Node Medic is set up." in g and "--check" in g
    assert "<user>@<hostname>.local" in g and "nodemedic@<" not in g
    assert "--user <your user>" in g


def test_the_readme_puts_the_build_block_on_the_first_screen():
    r = _src("README.md")
    first = "\n".join(r.splitlines()[:45])
    assert "**Build your own medic.**" in first
    assert "scripts/setup_medic.py" in first and "Node Medic is set up." in first
    assert "nodemedic@<" not in r                       # any user name, really
    assert "ui_busy_guard.sh && sudo systemctl restart reticulum-node-medic" in r
    assert r.count("not yet been run end-to-end on a Pi 5") == 0


def test_the_tracker_doc_says_the_ceramic_antenna_is_the_default():
    t = _src("docs/TRACKER_GNSS.md")
    head = "\n".join(t.splitlines()[:12])
    assert "ceramic" in head and "buy nothing" in head and "microsoldering" in head
    assert "### Optional, advanced: an external GNSS antenna (microsoldering)" in t


def test_the_tour_page_and_the_gnss_diagnostic_name_the_right_socket():
    assert "socket marked LoRa" in _src("ui/setup_flow.py")
    assert "Press nothing on the board" in _src("ui/setup_flow.py")
    assert "nothing goes on the GNSS" in _src("diagnostics/gnss.py")
