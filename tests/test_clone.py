import json

import pytest

from node_profile import NodeProfile
from transport.connection import EmulatedConnection
from monitor.health_beacon import encode, decode
from monitor.registry import NodeRegistry
from workflows.clone import CloneWorkflow, CLONE_DIR

HASH = "eabdd142596bcae888242ec1b172d566"
PI5_CPUINFO = "Model : Raspberry Pi 5 Model B Rev 1.0"

EXPECTED_STEPS = [
    "verify_target_pi5",
    "copy_os_config",
    "copy_asset_store",
    "copy_monitoring_db",
    "generate_fresh_identity",
    "final_verification",
]


def registry_with_node():
    r = NodeRegistry()
    r.register(HASH, name="TRUTH", location="Northcote")
    kw = dict(uptime_s=36, heap_kb=140, wifi_rssi_dbm=-62, reset_reason=0,
              wifi_up=True, lora_up=True, tcp_backbone_up=True,
              local_tcp_server_up=True, wdt_armed=True, psram=True, fault=False,
              board_id=0x3F, fw=(0, 6, 2))
    r.ingest(HASH, decode(encode(**kw)), 1_000_000.0)
    return r


def conn(cpuinfo=PI5_CPUINFO):
    c = EmulatedConnection(default_code=0, default_stdout="ok")
    c.rules.insert(0, ("/proc/cpuinfo", 0, cpuinfo, ""))
    return c


def wf(c=None, registry=None):
    return CloneWorkflow(c or conn(), registry or registry_with_node())


def test_steps_registered_in_order():
    assert [n for n, _ in wf().steps] == EXPECTED_STEPS


def test_full_run_completes():
    w = wf()
    w.run_all()
    assert w.current_index == len(EXPECTED_STEPS)
    assert all(r.success for r in w.results)


def test_verify_fails_on_non_pi5():
    w = wf(conn(cpuinfo="Model : Raspberry Pi 4 Model B"))
    r = w.steps[0][1](w)
    assert r.success is False


def test_copy_monitoring_db_writes_registry_json():
    c = conn()
    w = wf(c)
    for i in range(4):        # up to copy_monitoring_db
        w.steps[i][1](w)
    # the registry JSON was written to the clone dir on the target
    write_cmd = next(cmd for cmd in c.history if CLONE_DIR in cmd and "TRUTH" in cmd)
    payload = w.monitoring_db_json
    assert "TRUTH" in payload
    # it is valid JSON carrying our node
    data = json.loads(payload)
    assert data["nodes"][0]["name"] == "TRUTH"


def test_generate_fresh_identity_does_not_copy_source():
    c = conn()
    w = wf(c)
    w.steps[0][1](w)
    r = w.steps[4][1](w)      # generate_fresh_identity
    assert r.success
    assert w.fresh_identity_generated is True
    gen_cmd = next(cmd for cmd in c.history if "identity" in cmd.lower())
    assert "generate" in gen_cmd.lower()
    # never pushes/copies an existing identity file onto the target
    assert not any("push" in cmd for cmd in c.history)
    assert not any(("cp " in cmd or "scp" in cmd) and "identity" in cmd
                   for cmd in c.history)


def test_run_all_stops_on_verify_failure():
    w = wf(conn(cpuinfo="not a pi"))
    w.run_all()
    assert w.current_index == 0
    assert w.results[-1].name == "verify_target_pi5"
    assert w.results[-1].success is False
