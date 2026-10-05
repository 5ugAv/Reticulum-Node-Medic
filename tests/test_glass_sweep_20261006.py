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


def test_the_beacon_list_names_a_nameless_device_like_vitals_does():
    a = _src("ui/app.py")
    body = a[a.index("def _target_names"):a.index("def _lighthouse")]
    assert "rec._nameless_label()" in body and 'f"node {h[:8]}"' in body


def test_the_clone_asks_about_the_fleet_and_honours_the_answer():
    """The keeper (2026-10-06): a choice to bring the fleet or start fresh,
    and a Wi-Fi stage that says any network or a hotspot can be typed."""
    from workflows.clone import CloneWorkflow, copy_monitoring_db, copy_kin_roster
    from monitor.registry import NodeRegistry
    class _C:
        def run(self, *a, **k): return (0, "", "")
        def push_file(self, *a, **k): return True
    wf = CloneWorkflow(_C(), NodeRegistry(), fresh_fleet=True)
    for step in (copy_monitoring_db, copy_kin_roster):
        r = step(wf)
        assert r.success and r.skipped and "Fresh fleet" in r.message, step.__name__
    assert CloneWorkflow(_C(), NodeRegistry()).fresh_fleet is False
    s = _src("ui/screens/mitosis_screen.py")
    assert '"fleet": (1, 5, 7)' in s and '"wifi": (1, 6, 7)' in s and '"write": (1, 7, 7)' in s
    assert "def _show_stage_fleet" in s and 'fresh_fleet=getattr(self, "_fresh_fleet", False)' in s
    assert "a phone hotspot works" in s
    a = _src("ui/app.py")
    assert "def _mitosis_factory(hostname: str = \"\", fresh_fleet: bool = False)" in a
    assert "fresh_fleet=fresh_fleet" in a


def test_the_deb_cache_can_hold_the_whole_closure_for_an_offline_clone():
    from workflows.wheelhouse import closure_packages_command, cache_debs
    cmd = closure_packages_command(("cage",))
    assert "apt-cache depends --recurse" in cmd and "--no-recommends" in cmd and "cage" in cmd
    class _C:
        def __init__(self): self.cmds = []
        def run(self, c, timeout=None):
            self.cmds.append(c)
            if "apt-cache depends" in c: return (0, "cage\nlibwlroots-0.20\nlibc6\n", "")
            return (0, "", "")
    c = _C()
    ok, msg = cache_debs(c, packages=("cage",), dest="/tmp/x", closure=True)
    assert any("libwlroots-0.20" in x and "libc6" in x and "--print-uris" in x for x in c.cmds)
    assert "--closure" in _src("scripts/refresh_deb_cache.py")



def test_the_already_booted_road_asks_the_fleet_question_too():
    s = _src("ui/screens/mitosis_screen.py")
    nc = s[s.index("def _name_continue"):s.index("def _show_stage_password")]
    assert "self._show_stage_fleet()" in nc and "self._show_stage_clone()" not in nc
    fc = s[s.index("def _fleet_continue"):s.index("def _show_stage_wifi")]
    assert "self._show_stage_clone()" in fc and "self._show_stage_wifi()" in fc



def test_the_retry_road_is_not_called_skip_and_asks_first():
    s = _src("ui/screens/mitosis_screen.py")
    assert "skip to the clone" not in s
    assert "retry.bind(on_release=lambda *_: self._confirm_retry_road())" in s
    c = s[s.index("def _confirm_retry_road"):s.index("def _start_card_poll")]
    assert "_show_stage_name(skip_mode=True)" in c and 'tr("No — I have a blank card")' in c



def test_the_activity_banner_takes_its_own_space():
    a = _src("ui/app.py")
    show = a[a.index("def _show_activity_banner"):a.index("def _hide_activity_banner")]
    hide = a[a.index("def _hide_activity_banner"):a.index("def _reserve_banner_space")]
    assert "self._reserve_banner_space(bar.height)" in show
    assert "self._reserve_banner_space(0)" in hide



def test_the_clone_gives_cage_an_empty_default_cursor():
    s = _src("workflows/clone.py")
    assert "~/.icons/default/cursors" in s and "ExecStart=/usr/bin/cage -s -- /bin/bash" in s
