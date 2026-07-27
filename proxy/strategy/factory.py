# proxy/strategy/factory.py
from __future__ import annotations

from .base import BaseInstanceStrategy
from .kv_aware import KVAwareStrategy
from .least_load import LeastLoadStrategy
from .round_robin import RoundRobinStrategy


def build_instance_strategy(name: str) -> BaseInstanceStrategy:
    n = (name or "").strip().lower()
    if n in ("rr", "round_robin", "round-robin"):
        return RoundRobinStrategy()
    if n in ("ll", "least_load", "least-load"):
        return LeastLoadStrategy()
    if n in ("kv_aware", "kv-aware", "kva", "cache_aware", "cache-aware"):
        return KVAwareStrategy()
    raise ValueError(f"unknown instance strategy: {name}")
