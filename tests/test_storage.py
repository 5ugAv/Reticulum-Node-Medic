"""Storage usage primitives (Settings item 6)."""

from provisioning import storage


def test_path_size_of_file_and_dir(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"x" * 100)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.bin").write_bytes(b"y" * 50)
    assert storage.path_size(str(tmp_path / "a.bin")) == 100
    assert storage.path_size(str(tmp_path)) == 150          # recursive
    assert storage.path_size(str(tmp_path / "nope")) == 0   # missing -> 0


def test_paths_size_combines(tmp_path):
    (tmp_path / "a").write_bytes(b"x" * 10)
    (tmp_path / "b").write_bytes(b"y" * 20)
    assert storage.paths_size([str(tmp_path / "a"), str(tmp_path / "b"),
                               str(tmp_path / "missing")]) == 30


def test_disk_usage_keys_and_percent(tmp_path):
    u = storage.disk_usage(str(tmp_path))
    assert set(u) == {"total", "used", "free", "percent"}
    assert u["total"] > 0
    assert 0 <= u["percent"] <= 100


def test_disk_usage_missing_path_is_zeroed():
    u = storage.disk_usage("/no/such/mount/point/xyz")
    assert u == {"total": 0, "used": 0, "free": 0, "percent": 0}


def test_format_size():
    assert storage.format_size(0) == "0 B"
    assert storage.format_size(1536) == "1.5 KB"
    assert storage.format_size(177 * 1024 * 1024) == "177.0 MB"
    assert storage.format_size(14 * 1024 ** 3) == "14.0 GB"
