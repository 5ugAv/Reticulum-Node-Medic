"""Tool-wide radio defaults + regional presets (Settings item 1)."""

from provisioning import radio_defaults as rd


def test_every_regional_preset_fills_all_five_params():
    for key in rd.preset_keys():
        p = rd.preset_params(key)
        assert p is not None
        assert set(p) == set(rd.PARAM_KEYS)              # all five, no more
        # sane ranges
        assert 100.0 < p["freq"] < 1000.0
        assert p["bw"] in (7.8, 10.4, 15.6, 20.8, 31.25, 41.7, 62.5, 125.0, 250.0, 500.0)
        assert 6 <= p["sf"] <= 12
        assert 5 <= p["cr"] <= 8
        assert 0 < p["txp"] <= 30


def test_expected_regions_present_with_frequencies():
    freqs = {rd.preset_label(k): rd.preset_params(k)["freq"] for k in rd.preset_keys()}
    assert freqs["Australia / New Zealand / Americas"] == 915.125
    assert freqs["Europe (EU868)"] == 869.525
    assert freqs["India"] == 866.0
    assert freqs["Asia (varies)"] == 923.0


def test_americas_preset_equals_canonical_default():
    assert rd.preset_params("au_nz_americas") == rd._coerce(rd.DEFAULT_PARAMS)


def test_preset_params_is_a_copy():
    p = rd.preset_params("eu868")
    p["freq"] = 1.0
    assert rd.preset_params("eu868")["freq"] == 869.525    # source untouched


def test_unknown_preset_is_none():
    assert rd.preset_params("mars") is None
    assert rd.preset_label("mars") is None
    assert rd.preset_note("mars") is None


def test_load_defaults_missing_file_is_canonical(tmp_path):
    d = rd.load_defaults(path=str(tmp_path / "none.json"))
    assert d == rd._coerce(rd.DEFAULT_PARAMS)


def test_save_and_load_roundtrip(tmp_path):
    p = str(tmp_path / "rd.json")
    rd.save_defaults({"freq": 868.1, "bw": 250.0, "sf": 7, "cr": 6, "txp": 20}, path=p)
    d = rd.load_defaults(path=p)
    assert d == {"freq": 868.1, "bw": 250.0, "sf": 7, "cr": 6, "txp": 20}


def test_garbled_values_fall_back_per_key(tmp_path):
    p = str(tmp_path / "rd.json")
    # freq garbled, rest valid -> freq falls back to canonical, others kept
    rd.save_defaults({"freq": "abc", "bw": 125, "sf": 10, "cr": 5, "txp": 14}, path=p)
    d = rd.load_defaults(path=p)
    assert d["freq"] == rd.DEFAULT_PARAMS["freq"]
    assert d["sf"] == 10 and d["txp"] == 14


def test_load_corrupt_json_is_canonical(tmp_path):
    p = tmp_path / "rd.json"
    p.write_text("{not json")
    assert rd.load_defaults(path=str(p)) == rd._coerce(rd.DEFAULT_PARAMS)


def test_summary_matches_birth_style():
    assert rd.summary(rd.DEFAULT_PARAMS) == "915.125 MHz / BW125 / SF9 / CR5 / 17 dBm"
