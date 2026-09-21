"""Ping node now: when the path ran through a relay and no reply was heard,
the screen names THAT — not the throttle. Operator, 2026-09-21: ROOFRAK a
foot from the medic, "Path found — 2 hop(s) away" then "did not answer —
nodes throttle repeat replies", which was true in general and wrong that
night: the medic could not decode a radio that close and the mesh routed
via another node. Pinned in code."""
import re


def test_relayed_silence_names_the_relay_and_the_near_field():
    src = open("ui/app.py").read()
    i = src.index("elif outcome == DELIVERY_UNANSWERED:")
    branch = src[i:i + 2500]
    assert "hops >= 2" in branch
    # the sentences are split across source lines; pin contiguous fragments
    assert "is not hearing" in branch and "this node directly" in branch
    assert "too loud to" in branch and "decode. Move it" in branch
    # the throttle sentence survives for the direct case
    assert "nodes throttle repeat" in branch
