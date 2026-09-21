"""RTNode-2400-NM: family key unchanged, the build's name carried by the
certificate and shown on the node's page (decided 2026-09-21)."""
from provisioning.board_coverage import classify_cert


def test_a_new_certificate_names_the_build_and_keeps_the_family():
    f = classify_cert({"board": "Heltec Mesh Node T114", "firmware": "1.85",
                       "node_type": "rtnode2400", "firmware_name": "RTNode-2400-NM"})
    assert f.kind == "rtnode2400" and f.name == "RTNode-2400-NM"


def test_an_old_certificate_has_no_name_and_the_family_stands():
    f = classify_cert({"board": "Ebyte EoRa-S3", "firmware": "0.7.0"})
    assert f.kind == "rtnode2400" and f.name == ""


def test_the_medic_stamps_the_name_at_birth():
    src = open("workflows/rtnode_build.py").read()
    assert '"firmware_name": "RTNode-2400-NM"' in src
    assert '"firmware_variant": "nm"' in src


def test_the_node_page_prefers_the_builds_own_name():
    src = open("ui/screens/node_detail_screen.py").read()
    assert "fact.name or kinds.get(fact.kind)" in src
