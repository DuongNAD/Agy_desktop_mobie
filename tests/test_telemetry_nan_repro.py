"""
Empirical demonstration of NaN JSON parsing vulnerability.
"""

import json
import math
import pytest
from backend.telemetry.monitor import SystemMonitor

def test_clamp_nan_behavior():
    # 1. SystemMonitor._clamp with NaN
    clamped_nan = SystemMonitor._clamp(float("nan"))
    assert math.isnan(clamped_nan), "Expected current implementation to leak NaN"

    # 2. JSON Serialization of NaN
    payload = {"type": "telemetry", "cpu": clamped_nan}
    serialized = json.dumps(payload)
    assert "NaN" in serialized

    # 3. Strict RFC 8259 JSON compliance check
    # Python json.loads with parse_constant that rejects NaN or standard JS parser simulation
    def raise_nan(c):
        raise ValueError(f"Strict JSON specification violation: non-compliant constant '{c}'")

    with pytest.raises(ValueError, match="Strict JSON specification violation"):
        json.loads(serialized, parse_constant=raise_nan)
