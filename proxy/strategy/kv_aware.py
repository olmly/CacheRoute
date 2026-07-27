"""Cache-aware proxy routing strategy with least-load fallback."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .base import BaseInstanceStrategy, InstanceLike
from .least_load import LeastLoadStrategy


class KVAwareStrategy(BaseInstanceStrategy):
    """Prefer instances with likely prefix residency while avoiding overloaded nodes."""

    name = "kv_aware"

    def __init__(self) -> None:
        self._fallback = LeastLoadStrategy()

    def select(self, instances: List[InstanceLike], hint: Optional[Any] = None) -> InstanceLike:
        if not instances:
            raise RuntimeError("no instances")

        cache_lookup = hint.get("cache_lookup") if isinstance(hint, dict) else None
        prefix_matches = cache_lookup.get("matches") if isinstance(cache_lookup, dict) else None
        prefix_map = {
            str(item.get("instance_id")): item
            for item in (prefix_matches or [])
            if isinstance(item, dict) and item.get("instance_id")
        }
        if not prefix_map:
            return self._fallback.select(instances, hint=hint)

        scored = []
        for instance in instances:
            detail = self.compute_score(instance, hint=hint)
            total = detail.get("route_score")
            if total is not None:
                scored.append((instance, detail))
        if not scored:
            return self._fallback.select(instances, hint=hint)

        best_score = max(float(detail["route_score"]) for _, detail in scored)
        ties = [instance for instance, detail in scored if float(detail["route_score"]) == best_score]
        if len(ties) == 1:
            return ties[0]
        return self._fallback.select(ties, hint=hint)

    def compute_score(self, instance: InstanceLike, hint: Optional[Any] = None) -> Dict[str, Any]:
        fallback = self._fallback.compute_score(instance, hint=hint)
        cache_lookup = hint.get("cache_lookup") if isinstance(hint, dict) else None
        matches = cache_lookup.get("matches") if isinstance(cache_lookup, dict) else []
        match_map = {
            str(item.get("instance_id")): item
            for item in matches
            if isinstance(item, dict) and item.get("instance_id")
        }
        match = match_map.get(getattr(instance, "instance_id", ""))
        if not isinstance(match, dict):
            return {
                "route_score": None,
                "residency_score": None,
                "pressure_score": _pressure_from_fallback(fallback),
                "fallback": fallback,
                "reason": "no_prefix_match",
            }

        residency_score = _as_float(match.get("residency_score")) or 0.0
        pressure_score = _pressure_from_instance(instance, fallback)
        route_score = (residency_score * 0.7) - (pressure_score * 0.3)
        return {
            "route_score": route_score,
            "residency_score": residency_score,
            "pressure_score": pressure_score,
            "fallback": fallback,
            "reason": "cache_match",
            "match": match,
        }


def _pressure_from_instance(instance: InstanceLike, fallback: Dict[str, Any]) -> float:
    cache_metrics = getattr(instance, "cache_metrics", None)
    cache_pressure = _as_float(getattr(cache_metrics, "pressure_score", None))
    if cache_pressure is not None:
        return max(0.0, min(1.0, cache_pressure))
    return _pressure_from_fallback(fallback)


def _pressure_from_fallback(fallback: Dict[str, Any]) -> float:
    total = _as_float(fallback.get("total"))
    if total is None or total <= 0:
        return 0.0
    # Saturate fallback load into 0..1 so cache routing stays bounded.
    return max(0.0, min(1.0, total / 10.0))


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
