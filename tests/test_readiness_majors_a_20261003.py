"""Batch A of the 2026-10-03 readiness sweep's confirmed majors, pinned at source."""
import os


def _src(p): return open(p).read()


def test_map_plot_has_the_refresh_the_toggles_call():
    s = _src("ui/screens/scan_screen.py")
    assert "    def refresh(self):" in s and "self.plot.refresh()" in s


def test_field_readiness_counts_the_map_where_it_lives():
    s = _src("workflows/carry.py")
    assert "MAPS_DIR" in s and "~/reticulum-tool/assets/maps/*.mbtiles" not in s


def test_node_page_connections_lookup_uses_the_identity_key():
    s = _src("ui/app.py")
    assert 'd.get("identity") == rec.dst_hash' in s and 'd.get("dst_hash") == rec.dst_hash' not in s


def test_vitals_receives_an_empty_dashboard_and_refreshes_after_forget():
    s = _src("ui/app.py")
    i = s.index("dicts = self.monitor_service.dashboard_dicts()\n")
    assert "if dicts:" not in s[i:i + 300]
    f = s[s.index("def _forget_node"):]
    assert "self.vitals_screen.set_nodes(self.monitor_service.dashboard_dicts())" in f[:f.index('self.switch_mode("vitals")')]


def test_keep_it_reads_the_port_the_reader_stored():
    assert 'port = c.get("_port") or c.get("port") or ""' in _src("ui/screens/birth_guide_screen.py")


def test_pi_path_choose_manually_goes_to_the_catalogue():
    s = _src("ui/screens/birth_guide_screen.py")
    i = s.index("def _choose_manually")
    j = s.index("\n    def ", i + 10)
    assert '_pi_flash_radio' in s[i:j]


def test_chat_rows_escape_untrusted_text_and_keep_the_compose_field():
    from monitor.lxmf_chat import escape_markup
    assert escape_markup("[size=99]x[/size] & y") == "&bl;size=99&br;x&bl;/size&br; &amp; y"
    s = _src("ui/screens/chat_screen.py")
    assert s.count("escape_markup") >= 3
    assert 'if getattr(self, "_compose", None) is None:' in s
    assert 'if getattr(self, "_to", None) is None:' in s
    assert "send.disabled = not running" in s


def test_toast_titles_say_what_the_toast_is_about():
    from monitor.formatting import toast_title
    assert toast_title("Backpack mode — transport OFF. Safe to move.", True, "backpack") == "Backpack mode"
    assert toast_title("Home mode — full propagation node.", True, "home") == "Home mode"
    assert toast_title("Battery low — plug Node Medic in soon", False) == "Battery"
    assert toast_title("Two power supplies — shutting down safely.", False) == "Power"
    assert toast_title("Second supply removed — all safe.", True) == "Power"
    assert toast_title("skyfinger has been unreachable for 3 days — it likely needs a physical check.", False) == "Node down"
    assert toast_title("Mode change hit a problem: rnsd restart FAILED", False) == "Mode change"
    s = _src("ui/app.py")
    assert "toast_title(message, ok, mode)" in s and "self._refresh_node_mode()" in s[s.index("def done(dt):"):s.index("def done(dt):") + 600]


def test_wizard_vault_check_asks_the_records_vault():
    s = _src("ui/app.py"); b = s[s.index("def _vault_exists"):s.index("def _vault_exists") + 700]
    assert "records_vault" in b and "CONTAINER_PATH" not in b


def test_datetime_shows_now_on_entry_and_sets_the_clock_only_when_edited():
    s = _src("ui/screens/datetime_screen.py")
    assert "    def enter(self):" in s and 'dt_val != getattr(self, "_dt_shown", None)' in s
    assert "datetime_scr.bind(on_enter=lambda *_: self.datetime_screen.enter())" in _src("ui/app.py")


def test_brightness_failure_is_spoken():
    assert "Brightness isn't controllable on this display: " in _src("ui/screens/settings_screen.py")


def test_node_page_answering_means_recently_heard():
    s = _src("ui/screens/node_detail_screen.py")
    assert "answering = _seen_h is not None and _seen_h <= theme.NOT_HEARD_ALERT_HOURS" in s


def test_recovery_key_shown_again_is_the_same_key():
    assert 'RecoveryKeyScreen(key=getattr(self, "_recovery", None) or None' in _src("ui/screens/setup_wizard_screen.py")
    e = _src("ui/screens/encryption_screen.py")
    assert 'if not getattr(self, "_recovery", None):\n            self._recovery = ef.new_recovery_key()' in e


def test_no_text_sends_a_stranger_to_a_probe_card_that_does_not_exist():
    for p in ("ui/setup_flow.py", "ui/rebirth_advice.py"):
        s = _src(p)
        assert "toolbox" not in s and "PROBE ▸" not in s and "later from PROBE" not in s


def test_vitals_says_something_when_empty():
    s = _src("ui/screens/vitals_screen.py")
    assert "No nodes yet. Build one in BUILD" in s and "No node matches this filter." in s
