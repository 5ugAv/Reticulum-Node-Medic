"""The 2026-10-06 glass sweep (four reviewers over 33 captured screens) and
the keeper's two asks: the board's name dominates its confirm gate, and the
keeper's own credit says what they did. Source-level pins."""
import pathlib


def _src(p):
    return pathlib.Path(p).read_text()


def test_settings_state_lines_grow_to_their_text():
    s = _src("ui/screens/settings_screen.py")
    body = s[s.index("def _encryption_entry"):s.index("def refresh_encryption_row")]
    assert body.count("grow_to_text(") >= 3 and 'h=theme.line_dp("12.5sp")' not in body
    alerts = s[s.index('tr("Alerts")'):s.index("def _retention_section")]
    assert 'row.bind(minimum_height=row.setter("height"))' in alerts


def test_vitals_services_tag_is_sized_to_its_words():
    s = _src("ui/screens/vitals_screen.py")
    i = s.index('tr("x{n} services")')
    assert "size_hint_x=None" in s[i:i + 400] and 'setattr(i, "width", ts[0]' in s[i:i + 700]


def test_seen_speaks_minutes_under_the_hour():
    from monitor.formatting import seen_and_echo
    assert seen_and_echo({"last_seen_hours": 0.05}) == ("SEEN 3m", None)
    assert seen_and_echo({"last_seen_hours": 3.1}) == ("SEEN 3.1h", None)


def test_antenna_guidance_grows_and_the_beacon_list_is_capped():
    t = _src("ui/screens/triage_screen.py")
    assert 'texture_size=lambda i, ts: setattr(\n            i, "height", max(dp(40), ts[1] + dp(4)))' in t
    a = _src("ui/app.py")
    body = a[a.index("def _target_names"):a.index("def _lighthouse")]
    assert 'tr("{a}, {b} and {n} more")' in body


def test_radio_defaults_save_sits_outside_the_scroll():
    s = _src("ui/screens/radio_defaults_screen.py")
    i = s.index("body.add_widget(col)")
    assert "self.add_widget(save)" in s[i:i + 400]
    assert "col.add_widget(save)" not in s


def test_notifications_status_wraps_and_the_address_fits():
    s = _src("ui/screens/notifications_screen.py")
    assert 'self.status = _lbl("", size="13sp")' in s and 'font_size="21sp"' in s


def test_small_text_fits():
    assert 'strftime("%d\\u00a0%b\\u00a0%Y")' in _src("ui/screens/trusted_operators_screen.py")
    assert 'mono=True, size="13sp")' in _src("ui/screens/about_screen.py")
    assert _src("ui/screens/birth_screen.py").count("hint.padding = (dp(22), 0)") == 2


def test_the_board_name_dominates_both_confirm_gates():
    s = _src("ui/screens/birth_screen.py")
    for fn in ("def _confirm_board_gate", "def _confirm_rnode_board_gate"):
        i = s.index(fn); body = s[i:s.index("\n    def ", i + 10)]
        assert 'font_size="34sp"' in body, fn
        assert body.index('font_size="34sp"') < body.index("WARNING:  Selecting the wrong board"), fn
        assert "Confirm you've selected the correct board" not in body
        assert 'tr("Is this the board in your hand?")' in body
    g = _src("ui/screens/birth_guide_screen.py")
    assert '_line(name, bold=True, size="26sp", h=44)' in g


def test_the_keepers_credit_names_the_work():
    # source-level: no test imports a Kivy screen
    s = _src("ui/screens/credits_screen.py")
    me = s[s.index("Concept and direction."):s.index('"5ugAv"')]
    for word in ("bench", "RNode", "RTNode", "antenna", "clone", "firstborn", "front page"):
        assert word in me, word
    assert 'max(dp(52), ts[1] + dp(8))' in s      # rows grow to a paragraph
