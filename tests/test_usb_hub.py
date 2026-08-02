"""A powered hub un-refuses a pairing — but only via a question, never silently."""

import os

from provisioning.usb_hub import (external_hubs, is_self_powered,
                                  powered_hub_present, hub_question)


def _dev(root, name, cls="09", bm="e0", product="Hub", max_power="100mA"):
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    for f, v in (("bDeviceClass", cls), ("bmAttributes", bm),
                 ("product", product), ("bMaxPower", max_power)):
        with open(os.path.join(d, f), "w") as fh:
            fh.write(v + "\n")


def test_self_powered_bit(tmp_path):
    assert is_self_powered("e0") is True        # bit 6 set (root hubs, medic)
    assert is_self_powered("a0") is False       # bus-powered
    assert is_self_powered("") is None          # unreadable -> unknown
    assert is_self_powered("zz") is None


def test_root_hubs_are_not_counted_as_a_plugged_in_hub(tmp_path):
    """usb1/usb2 are the Pi's own controllers. Counting them would mean the
    medic always believed a powered hub was present and never warned at all."""
    root = str(tmp_path)
    _dev(root, "usb1"); _dev(root, "usb2")
    assert external_hubs(root) == []
    assert powered_hub_present(root) is None


def test_an_external_self_powered_hub_is_found(tmp_path):
    root = str(tmp_path)
    _dev(root, "usb1")                               # root hub, ignored
    _dev(root, "1-1", bm="e0", product="4-Port Hub")
    hub = powered_hub_present(root)
    assert hub and hub["product"] == "4-Port Hub"


def test_a_bus_powered_hub_is_not_treated_as_powered(tmp_path):
    root = str(tmp_path)
    _dev(root, "1-1", bm="a0", product="Cheap Hub")
    assert powered_hub_present(root) is None


def test_no_evidence_reads_the_same_as_no_hub(tmp_path):
    """An unreadable sysfs must never be read as permission to proceed."""
    assert powered_hub_present(str(tmp_path / "does-not-exist")) is None


def test_the_operator_is_asked_whether_the_hub_travels(tmp_path):
    """The brown-out happens in the FIELD. A hub seen on the bench proves the
    operator owns one, not that the node ships with it — so this must read as a
    question with the consequence spelled out, never as a silent override."""
    q = hub_question({"product": "4-Port Hub"}, "Pi Zero 2 W", "Heltec V4")
    assert "?" in q["head"] or "connected" in q["head"]
    assert "finished node" in q["body"]
    assert "stays with the node" in q["confirm"]
    assert "bench" in q["note"] and "brown out" in q["note"]
