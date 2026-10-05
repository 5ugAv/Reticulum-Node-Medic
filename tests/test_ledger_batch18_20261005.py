"""Readiness ledger #215: messages composed OUTSIDE ui/ reach the glass through
the catalog — the producers take an optional ``translate`` and the screens
pass ``tr``. Source-level pins (Kivy screens are not importable here)."""
import pathlib


def _src(p):
    return pathlib.Path(p).read_text()


def test_the_clock_messages_take_a_translator():
    s = _src("provisioning/tool_datetime.py")
    for fn in ("set_datetime", "set_timezone", "sync_from_gps", "gps_time_or_reason",
               "format_synced_ago"):
        i = s.index(f"def {fn}(")
        assert "translate=None" in s[i:s.index(")", i) + 1], fn
    assert '"GPS-synced"' in s and 'replace("synced"' not in _src("ui/screens/datetime_screen.py")
    d = _src("ui/screens/datetime_screen.py")
    assert d.count("translate=tr") >= 5


def test_the_clock_messages_are_unchanged_in_english():
    from provisioning import tool_datetime as td
    assert td.format_synced_ago(None, 100.0) == "never synced"
    assert td.format_synced_ago(90.0, 100.0) == "synced just now"
    assert td.format_synced_ago(100.0, 100.0 + 3 * 3600, source="GPS") == "GPS-synced 3 hours ago"
    ok, msg = td.set_timezone("", run=lambda c: (0, ""))
    assert (ok, msg) == (False, "No timezone given.")
    ok, msg = td.set_timezone("Australia/Melbourne", run=lambda c: (0, ""),
                              translate=lambda t: "[" + t + "]")
    assert msg == "[Timezone set to {tz}.]".replace("{tz}", "Australia/Melbourne")


def test_the_encryption_messages_take_a_translator():
    s = _src("provisioning/encryption_flow.py")
    for fn in ("covered_lines", "blockers", "doors_to_set", "headline", "state",
               "confirm_problem", "recovery_problem", "passphrase_problem"):
        i = s.index(f"def {fn}(")
        assert "translate=None" in s[i:s.index(")", i) + 1], fn
    e = _src("ui/screens/encryption_screen.py")
    assert e.count("translate=tr") >= 5
    from provisioning import encryption_flow as ef
    assert ef.confirm_problem("a", "b") == "Those two do not match. Type it again."
    assert ef.confirm_problem("a", "b", translate=str.upper).startswith("THOSE TWO")
    assert ef.headline({"on": False, "doors": []}).startswith("Your records on this card are NOT")
    assert ef.headline({"on": True, "doors": ["passphrase"]}) == \
        "Your records on this card are encrypted. 1 way in: passphrase."


def test_the_salvage_screen_speaks_through_the_catalog():
    s = _src("ui/screens/salvage_screen.py")
    for needle in ("sv.summary(self._found, translate=tr)", "tr(p.title)", "tr(p.plain)",
                   "tr(p.caution)", "tr(g.title)", "tr(g.opening)", "tr(s.text)",
                   "tr(s.detail)", "tr(s.watch_out)", "tr(g.expect)", "tr(g.caution)",
                   "tr(q.text)", "tr(q.look_for)", "tr(text), lambda k=key"):
        assert needle in s, needle
    from provisioning import salvage as sv
    assert sv.summary(sv.Found()).startswith("Let's find out")
    f = sv.Found(kind="dev_board", chip="esp32s3", has_lora=True, has_usb=True, board_key="heltec32_v4")
    assert sv.summary(f) == "Good news — this is a board the medic already knows."
    wrapped = sv.summary(f, translate=lambda t: "[" + t + "]")
    assert wrapped == "[Good news — {title}.]".replace("{title}", "[This is a board the medic already knows]")


def test_trust_words_and_the_screensaver_label_are_translated_at_the_screen():
    assert 'via = tr(u["via"]) if u.get("via") else ""' in _src("ui/screens/trusted_operators_screen.py")
    assert "tr(ss.STYLE_LABELS.get(" in _src("ui/screens/settings_screen.py")


def test_last_heard_speaks_minutes_under_the_hour():
    s = _src("ui/screens/node_detail_screen.py")
    assert "format_age_fine(seen)" in s and "format_age_fine(mesh)" in s
