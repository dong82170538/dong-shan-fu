"""Adapter boundary. Only synthetic fixture computation is implemented."""
from typing import Protocol
import time
from .domain import evaluate


CAPABILITIES = {
    "test_fixture": {"available": True, "synthetic": True, "can_publish": False},
    "OZON": {"available": False, "can_publish": False, "reason": "No authorized official shop adapter configured"},
    "WB": {"available": False, "can_publish": False, "reason": "No authorized official shop adapter configured"},
}


class ProductAdapter(Protocol):
    """Future official adapters must supply normalized data and a capability grant.

    Queue acceptance, result persistence and remote publication are separate facts.
    This milestone has no publication method or credential storage.
    """
    def run(self, raw: dict, policy: dict, attempt: int) -> dict: ...


class FixtureAdapter:
    def run(self, raw, policy, attempt):
        behavior = raw.get("test_behavior", {})
        time.sleep(behavior.get("delay_ms", 0) / 1000)
        if attempt <= behavior.get("fail_attempts", 0):
            raise FixtureFailure()
        return evaluate(raw, policy)


class FixtureFailure(Exception):
    pass
