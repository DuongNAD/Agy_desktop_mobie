"""
Pure Python lightweight subscriber stress test for TelemetryPoller without mock overhead.
"""

import asyncio
import gc
import tracemalloc
import pytest

from backend.telemetry.monitor import TelemetryPoller

class DummyWebSocket:
    def __init__(self):
        self.received_count = 0

    async def send_json(self, payload: dict):
        self.received_count += 1

@pytest.mark.asyncio
async def test_pure_subscriber_churn_memory_stability():
    """Verify TelemetryPoller has zero internal leaks across 1,000 cycles."""
    poller = TelemetryPoller(poll_interval=0.01)
    await poller.start()

    tracemalloc.start()
    gc.collect()
    snapshot_start = tracemalloc.take_snapshot()

    active_subscribers = []

    for i in range(1000):
        # Create lightweight dummy websocket
        ws = DummyWebSocket()
        poller.register_subscriber(ws)
        active_subscribers.append(ws)

        # Broadcast
        await poller.broadcast({"type": "telemetry", "cycle": i})

        # Churn: drop half
        if i % 2 == 0 and active_subscribers:
            dropped = active_subscribers.pop(0)
            poller.unregister_subscriber(dropped)

    for ws in active_subscribers:
        poller.unregister_subscriber(ws)
    active_subscribers.clear()

    await poller.stop()
    gc.collect()
    snapshot_end = tracemalloc.take_snapshot()

    stats = snapshot_end.compare_to(snapshot_start, "lineno")
    total_diff_kb = sum(stat.size_diff for stat in stats) / 1024

    tracemalloc.stop()

    print(f"\n--- Total Diff: {total_diff_kb:.2f} KB across 1,000 cycles ---")
    for stat in stats[:5]:
        print(stat)

    backend_stats = [s for s in stats if "backend" in str(s.traceback)]
    backend_diff_kb = sum(stat.size_diff for stat in backend_stats) / 1024
    print(f"Backend Diff: {backend_diff_kb:.2f} KB")

    # Backend telemetry should have minimal overhead (< 50 KB)
    assert backend_diff_kb < 50.0, f"Backend memory growth exceeded limit: {backend_diff_kb} KB"
    assert total_diff_kb < 1000.0, f"Total memory growth exceeded limit: {total_diff_kb} KB"
    assert len(poller._subscribers) == 0
