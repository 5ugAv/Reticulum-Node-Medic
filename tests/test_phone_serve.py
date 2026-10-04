"""Phone-app serve — pure URL building + a real one-shot HTTP fetch."""

import os
import urllib.request

from workflows.phone_serve import build_url, AppServer, DEFAULT_PORT


def test_build_url_normal():
    assert build_url("192.168.1.51", "columba.apk") == \
        f"http://192.168.1.51:{DEFAULT_PORT}/columba.apk"
    assert build_url("10.0.0.2", "x.apk", port=9000) == "http://10.0.0.2:9000/x.apk"


def test_build_url_none_without_ip_or_file():
    assert build_url(None, "x.apk") is None
    assert build_url("192.168.1.1", "") is None


def test_server_serves_the_cache_dir(tmp_path):
    apk = tmp_path / "columba-test.apk"
    apk.write_bytes(b"PK\x03\x04 fake apk bytes")
    srv = AppServer(str(tmp_path), port=0, bind_host="127.0.0.1")   # no fixed port (#202)
    try:
        base = srv.start()
        assert srv.is_running() and srv.port > 0
        # base may be None on a CI box with no route; fetch via loopback regardless
        got = urllib.request.urlopen(
            f"http://127.0.0.1:{srv.port}/columba-test.apk", timeout=3).read()
        assert got == b"PK\x03\x04 fake apk bytes"
    finally:
        srv.stop()
    assert not srv.is_running()
