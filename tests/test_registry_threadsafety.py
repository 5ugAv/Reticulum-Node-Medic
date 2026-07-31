"""The registry is touched by three threads — it must not crash under them.

The RNS announce handler inserts new node keys while the monitor poll thread
saves/dashboards and the Kivy main thread renders VITALS/SCAN. Before the lock
this raised RuntimeError('dictionary changed size during iteration') within
milliseconds (2026-08-01 bug hunt: 169 crashes in a 4 s stress run; 0 after).
"""

import threading
import time

from monitor.registry import NodeRegistry


def test_concurrent_announces_and_reads_never_raise():
    reg = NodeRegistry()
    for i in range(150):
        reg.register(f"{i:032x}", name=f"n{i}")

    errors = []
    stop = threading.Event()

    def writer():
        i = 1000
        while not stop.is_set():
            try:
                reg.register(f"{i:032x}", name=f"late{i}")
                i += 1
            except Exception as e:            # noqa: BLE001
                errors.append(("writer", repr(e)))

    def reader():
        while not stop.is_set():
            try:
                reg.devices(time.time())
                reg.to_dict()
                reg.summary(time.time())
            except Exception as e:            # noqa: BLE001
                errors.append(("reader", repr(e)))

    threads = ([threading.Thread(target=writer, daemon=True) for _ in range(2)]
               + [threading.Thread(target=reader, daemon=True) for _ in range(3)])
    for t in threads:
        t.start()
    time.sleep(1.5)
    stop.set()
    for t in threads:
        t.join(timeout=3)

    assert not errors, f"{len(errors)} concurrency errors: {errors[:3]}"
