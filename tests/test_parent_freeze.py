"""A clone carries the parent's Python packages exactly as the parent runs
them (keeper, 2026-10-06: "how Node Medic 1 works, all of that is packaged up
and sent across"). Node Medic 2 got rns 1.3.8 from the downloads while its
parent ran a patched 1.3.7, and could not name its Tracker."""
import os
import zipfile

from workflows import parent_freeze as pf


def _fake_site(tmp_path, name="rns", version="1.3.7", patched_line="PRODUCT_X = 0xCB\n"):
    site = tmp_path / "site"
    pkg = site / "RNS"
    (pkg / "Utilities").mkdir(parents=True)
    (pkg / "__init__.py").write_text("__version__ = '1.3.7'\n")
    (pkg / "Utilities" / "rnodeconf.py").write_text("# tool\n" + patched_line)
    info = site / f"{name}-{version}.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    (info / "entry_points.txt").write_text("[console_scripts]\nrnodeconf = RNS.Utilities.rnodeconf:main\n")
    (info / "top_level.txt").write_text("RNS\n")
    (info / "RECORD").write_text(
        "RNS/__init__.py,,\nRNS/Utilities/rnodeconf.py,,\n"
        f"{name}-{version}.dist-info/METADATA,,\n../../../bin/rnodeconf,,\n")
    return site


def _dist(site, name="rns"):
    import importlib.metadata as md
    return next(d for d in md.distributions(path=[str(site)]) if d.metadata["Name"] == name)


def test_a_pure_package_is_repacked_from_the_installed_tree_patch_included(tmp_path):
    site = _fake_site(tmp_path)
    out = pf.repack_pure(_dist(site), str(tmp_path))
    assert os.path.basename(out) == "rns-1.3.7-py3-none-any.whl"
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        assert "RNS/Utilities/rnodeconf.py" in names
        assert "PRODUCT_X = 0xCB" in zf.read("RNS/Utilities/rnodeconf.py").decode()
        assert "rns-1.3.7.dist-info/WHEEL" in names and "rns-1.3.7.dist-info/RECORD" in names
        assert "rns-1.3.7.dist-info/entry_points.txt" in names
        assert not any(n.startswith("..") for n in names)       # console scripts: pip remakes them
        record = zf.read("rns-1.3.7.dist-info/RECORD").decode()
        assert "RNS/Utilities/rnodeconf.py,sha256=" in record and "RECORD,," in record
        assert "Root-Is-Purelib: true" in zf.read("rns-1.3.7.dist-info/WHEEL").decode()


def test_the_plan_repacks_pure_keeps_matching_compiled_and_names_the_missing():
    installed = {"rns": ("1.3.7", True), "Kivy": ("2.3.1", False), "Pillow": ("12.2.0", False)}
    wheels = ["/w/rns-1.3.8-py3-none-any.whl", "/w/Kivy-2.3.1-cp313-cp313-manylinux_2_17_aarch64.whl",
              "/w/pillow-12.1.0-cp313-cp313-manylinux_2_17_aarch64.whl"]
    repack, keep, missing = pf.plan(installed, wheels)
    assert repack == ["rns"]
    assert keep == ["/w/Kivy-2.3.1-cp313-cp313-manylinux_2_17_aarch64.whl"]
    assert missing == ["Pillow==12.2.0"]           # a compiled wheel it cannot build itself


def test_the_pins_name_the_parents_versions():
    txt = pf.requirements_text({"rns": ("1.3.7", True), "Kivy": ("2.3.1", False)})
    assert "Kivy==2.3.1" in txt and "rns==1.3.7" in txt


def test_the_clone_installs_the_parents_pins_when_they_travelled():
    from tests.srcutil import func_source
    body = func_source("workflows/clone.py", "install_dependencies")
    assert "requirements-parent" in body or "PARENT_REQUIREMENTS" in body
    assert "-r {pins}" in body


def test_no_pins_are_written_when_a_compiled_wheel_is_missing(tmp_path, monkeypatch):
    """A clone must never carry pins it cannot install offline."""
    wh = tmp_path / "wh"; wh.mkdir()
    (wh / pf.PARENT_REQUIREMENTS).write_text("stale\n")
    monkeypatch.setattr(pf, "runtime_names", lambda *a: ["Pillow"])
    monkeypatch.setattr(pf, "installed_versions", lambda names: {"Pillow": ("12.2.0", False)})
    ok, msg = pf.freeze(str(wh), str(tmp_path / "req.txt"))
    assert not ok and "Pillow==12.2.0" in msg
    assert not (wh / pf.PARENT_REQUIREMENTS).exists()


def test_the_clone_freezes_the_parent_before_copying_the_tool():
    from tests.srcutil import func_source
    body = func_source("workflows/clone.py", "transfer_tool")
    assert "_freeze_parent()" in body
    assert body.index("_freeze_parent()") < body.index("push_tree(")
    helper = func_source("workflows/clone.py", "_freeze_parent")
    assert "freeze(WHEELHOUSE, REQUIREMENTS)" in helper      # the runtime set, not --all


def test_field_readiness_freezes_the_parent_too():
    src = open("workflows/carry.py", encoding="utf-8").read()
    assert "parent_freeze import freeze" in src and "freeze(WHEELHOUSE, REQUIREMENTS)" in src


def test_os_packages_are_left_to_the_manifest(tmp_path):
    class _D:
        def __init__(self, name, ver, where):
            self.metadata = {"Name": name}; self.version = ver; self._w = where; self.files = []
        def locate_file(self, _p): return self._w
    dists = {"rns": _D("rns", "1.3.7", "/home/pi/.local/lib/python3.13/site-packages/"),
             "pyserial": _D("pyserial", "3.5", "/usr/lib/python3/dist-packages/")}
    got = pf.installed_versions(["rns", "pyserial"], lookup=lambda n: dists[n])
    assert got == {"rns": ("1.3.7", True)}
    txt = pf.requirements_text(got, ["rns==1.3.8", "Pillow==12.2.0", "smbus2==0.6.1"])
    assert "rns==1.3.7" in txt and "rns==1.3.8" not in txt
    assert "Pillow==12.2.0" in txt and "smbus2==0.6.1" in txt


def test_a_requirement_only_the_os_provides_blocks_the_pins():
    class _D:
        def __init__(self, reqs): self.requires = reqs
    esptool = _D(["pyyaml>=5.1", "intelhex", "pyserial>=3.3", "ecdsa; extra == 'hsm'",
                  'kivy-deps.angle~=0.4.0; sys_platform == "win32"',
                  'importlib-metadata; python_version < "3.8"'])
    unmet = pf.unmet_requirements([esptool], frozen=["esptool", "intelhex"],
                                  wheels=["/w/pyserial-3.5-py3-none-any.whl"], manifest=["rns==1.3.8"])
    assert unmet == ["pyyaml"]        # extras, Windows-only and Python<3.8 entries don't count here
    assert pf.unmet_requirements([esptool], ["esptool", "intelhex"],
                                 ["/w/pyserial-3.5-py3-none-any.whl", "/w/PyYAML-6.0-cp313-abi3-linux_aarch64.whl"], []) == []
