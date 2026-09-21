"""The node's page says what THIS medic built — board, firmware role, birth
date — from the certificate, not only what the node's beacon reports.
Operator, 2026-09-21: "we know what board this is, we can also put
information here." An RNode-firmware node reports nothing about itself;
without this its page had no board at all."""
import re

from ui.cert_store import cert_for_node


def _c(name, addr=None, ident=None, saved=1.0, **kw):
    return {"node_name": name, "reticulum_address": addr, "identity_hash": ident,
            "_saved_at": saved, **kw}


def test_cert_is_found_by_address_then_identity_then_name():
    certs = [_c("RAIN", addr="aa" * 16), _c("RAIN", ident="bb" * 16, saved=2.0),
             _c("T114", addr="cc" * 16)]
    assert cert_for_node(certs, dst_hash="AA" * 16)["reticulum_address"] == "aa" * 16
    assert cert_for_node(certs, dst_hash="bb" * 16)["identity_hash"] == "bb" * 16
    # a row keyed by name (the RTNode-by-name registry key) falls back to the
    # NEWEST certificate carrying that name — names are reused after rebirth
    assert cert_for_node(certs, dst_hash="rtnode:RAIN")["_saved_at"] == 2.0
    assert cert_for_node(certs, dst_hash="dd" * 16, name="RAIN")["_saved_at"] == 2.0
    assert cert_for_node(certs, dst_hash="dd" * 16, name="NOPE") is None
    assert cert_for_node([], dst_hash="aa" * 16) is None


def test_the_page_draws_its_birth_lines_from_the_certificate():
    src = open("ui/screens/node_detail_screen.py").read()
    m = re.search(r"    def _birth_lines\(.*?(?=\n    def )", src, re.S)
    assert m, "_birth_lines missing"
    body = m.group(0)
    assert "cert_for_node(" in body and "classify_cert(" in body
    assert "Built by this medic" in src
