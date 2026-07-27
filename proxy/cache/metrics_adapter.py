"""Fault-tolerant LMCache metrics adapter and poller."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

logger = logging.getLogger("proxy.cache.metrics_adapter")


_DEFAULT_CANDIDATE_PATHS = [
    "/metrics",
    "/debug/metrics",
    "/api/metrics",
    "/stats/metrics",
    "/internal/metrics",
]

_FIELD_ALIASES: Dict[str, List[str]] = {
    "hit_rate": ["hit_rate", "cache_hit_rate", "lmcache_hit_rate", "kv_cache_hit_rate"],
    "lookup_qps": ["lookup_qps", "lookup_rate", "lookup_requests_per_second", "lookup_rps"],
    "store_qps": ["store_qps", "store_rate", "store_requests_per_second", "store_rps"],
    "retrieve_qps": ["retrieve_qps", "retrieve_rate", "retrieve_requests_per_second", "retrieve_rps"],
    "eviction_rate": ["eviction_rate", "evict_rate", "evictions_per_second", "eviction_qps"],
    "avg_lookup_latency_ms": ["avg_lookup_latency_ms", "lookup_latency_ms", "lookup_latency_avg_ms"],
    "avg_store_latency_ms": ["avg_store_latency_ms", "store_latency_ms", "store_latency_avg_ms"],
    "avg_retrieve_latency_ms": ["avg_retrieve_latency_ms", "retrieve_latency_ms", "retrieve_latency_avg_ms"],
    "l1_usage_bytes": ["l1_usage_bytes", "l1_used_bytes", "gpu_usage_bytes", "gpu_used_bytes"],
    "l1_capacity_bytes": ["l1_capacity_bytes", "l1_total_bytes", "gpu_capacity_bytes", "gpu_total_bytes"],
    "l2_usage_bytes": ["l2_usage_bytes", "l2_used_bytes", "cpu_usage_bytes", "cpu_used_bytes", "disk_usage_bytes"],
    "l2_capacity_bytes": ["l2_capacity_bytes", "l2_total_bytes", "cpu_capacity_bytes", "cpu_total_bytes", "disk_capacity_bytes"],
    "active_sessions": ["active_sessions", "session_count", "active_requests", "inflight_sessions"],
}


@dataclass
class CacheMetricsSnapshot:
    instance_id: str
    source: str = "lmcache_metrics_adapter"
    fetched_at: int = field(default_factory=lambda: int(time.time()))
    status: str = "error"
    detail: Optional[str] = None
    source_endpoint: Optional[str] = None
    fetch_step: Optional[str] = None
    hit_rate: Optional[float] = None
    lookup_qps: Optional[float] = None
    store_qps: Optional[float] = None
    retrieve_qps: Optional[float] = None
    eviction_rate: Optional[float] = None
    avg_lookup_latency_ms: Optional[float] = None
    avg_store_latency_ms: Optional[float] = None
    avg_retrieve_latency_ms: Optional[float] = None
    l1_usage_bytes: Optional[float] = None
    l1_capacity_bytes: Optional[float] = None
    l2_usage_bytes: Optional[float] = None
    l2_capacity_bytes: Optional[float] = None
    active_sessions: Optional[int] = None
    pressure_score: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_state_snapshot(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["timestamp"] = payload.pop("fetched_at")
        return payload


async def fetch_instance_metrics(instance: Any, timeout_s: float = 2.5) -> CacheMetricsSnapshot:
    instance_id = str(getattr(instance, "instance_id", "") or "unknown_instance")
    urls = resolve_candidate_urls(instance)
    if not urls:
        return CacheMetricsSnapshot(
            instance_id=instance_id,
            status="error",
            detail="no_metrics_endpoint_configured",
            fetch_step="resolve",
        )

    errors: List[str] = []
    timeout = httpx.Timeout(timeout_s, connect=timeout_s)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for url in urls:
            try:
                response = await client.get(url)
                response.raise_for_status()
            except Exception as exc:
                errors.append(_short_error("fetch", url, exc))
                continue

            content_type = str(response.headers.get("content-type") or "").lower()
            body = response.text
            try:
                if "json" in content_type or body.lstrip().startswith(("{", "[")):
                    payload = response.json()
                    snapshot = normalize_metrics_payload(instance_id=instance_id, payload=payload, source_endpoint=url)
                else:
                    snapshot = normalize_metrics_payload(
                        instance_id=instance_id,
                        payload=_parse_prometheus_text(body),
                        source_endpoint=url,
                        raw_text=body,
                    )
            except Exception as exc:
                errors.append(_short_error("parse", url, exc))
                continue

            if snapshot.status == "error":
                errors.append(snapshot.detail or f"normalize:{url}")
                continue
            return snapshot

    return CacheMetricsSnapshot(
        instance_id=instance_id,
        status="error",
        detail="; ".join(errors[-3:]) if errors else "metrics_fetch_failed",
        source_endpoint=urls[0] if urls else None,
        fetch_step="fetch",
        raw={"errors": errors, "candidate_urls": urls},
    )


def normalize_metrics_payload(
    instance_id: str,
    payload: Any,
    source_endpoint: str,
    raw_text: Optional[str] = None,
) -> CacheMetricsSnapshot:
    flat = _flatten_payload(payload)
    values: Dict[str, Any] = {}
    for field_name, aliases in _FIELD_ALIASES.items():
        values[field_name] = _find_metric_value(flat, aliases)

    pressure_score = _derive_pressure_score(values)
    present_fields = [name for name, value in values.items() if value is not None]
    if pressure_score is not None:
        values["pressure_score"] = pressure_score
        present_fields.append("pressure_score")

    status = "error"
    detail = None
    if present_fields:
        status = "ok" if len(present_fields) >= 4 else "partial"
        if status == "partial":
            detail = f"partial_payload:{','.join(sorted(present_fields))}"
    else:
        detail = "normalize:no_supported_fields"

    raw_payload: Dict[str, Any]
    if isinstance(payload, dict):
        raw_payload = dict(payload)
    else:
        raw_payload = {"payload_type": type(payload).__name__}
    if raw_text is not None:
        raw_payload.setdefault("raw_text_sample", raw_text[:1000])

    return CacheMetricsSnapshot(
        instance_id=instance_id,
        status=status,
        detail=detail,
        source_endpoint=source_endpoint,
        fetch_step="normalize",
        hit_rate=_as_float(values.get("hit_rate")),
        lookup_qps=_as_float(values.get("lookup_qps")),
        store_qps=_as_float(values.get("store_qps")),
        retrieve_qps=_as_float(values.get("retrieve_qps")),
        eviction_rate=_as_float(values.get("eviction_rate")),
        avg_lookup_latency_ms=_as_float(values.get("avg_lookup_latency_ms")),
        avg_store_latency_ms=_as_float(values.get("avg_store_latency_ms")),
        avg_retrieve_latency_ms=_as_float(values.get("avg_retrieve_latency_ms")),
        l1_usage_bytes=_as_float(values.get("l1_usage_bytes")),
        l1_capacity_bytes=_as_float(values.get("l1_capacity_bytes")),
        l2_usage_bytes=_as_float(values.get("l2_usage_bytes")),
        l2_capacity_bytes=_as_float(values.get("l2_capacity_bytes")),
        active_sessions=_as_int(values.get("active_sessions")),
        pressure_score=_as_float(values.get("pressure_score")),
        raw=raw_payload,
    )


class LMCacheMetricsPoller:
    """Background and manual refresh collector for per-instance LMCache metrics."""

    def __init__(self, pool: Any, logger_: Optional[logging.Logger] = None) -> None:
        self._pool = pool
        self._logger = logger_ or logger
        self._interval_s = max(1.0, float(os.environ.get("PROXY_LMCACHE_METRICS_POLL_INTERVAL_S", "15") or 15.0))
        self._timeout_s = max(0.5, float(os.environ.get("PROXY_LMCACHE_METRICS_TIMEOUT_S", "2.5") or 2.5))

    @property
    def interval_s(self) -> float:
        return self._interval_s

    async def run_forever(self, stop_event: Any) -> None:
        while not stop_event.is_set():
            try:
                await self.refresh()
            except Exception:
                self._logger.warning("[Proxy][LMCache] poller refresh failed unexpectedly", exc_info=True)
            await stop_event.wait(self._interval_s)

    async def refresh(self, instance_ids: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        targets = self._select_targets(instance_ids)
        results: List[Dict[str, Any]] = []
        for instance in targets:
            snapshot = await fetch_instance_metrics(instance, timeout_s=self._timeout_s)
            previous = getattr(instance, "cache_metrics", None)
            previous_last_success = getattr(previous, "last_success_at", None)
            self._pool.report_cache_metrics(
                instance_id=instance.instance_id,
                snapshot=snapshot.to_state_snapshot(),
                metadata={
                    "source": snapshot.source,
                    "source_endpoint": snapshot.source_endpoint,
                    "fetch_step": snapshot.fetch_step,
                    "detail": snapshot.detail,
                },
            )
            current = self._find_instance(instance.instance_id)
            self._log_refresh_result(instance_id=instance.instance_id, snapshot=snapshot, previous_last_success=previous_last_success)
            if current is not None:
                results.append(_cache_metrics_to_public_dict(current.cache_metrics, instance_id=current.instance_id))
            else:
                results.append({"instance_id": instance.instance_id, "status": snapshot.status, "detail": snapshot.detail})
        return {"ok": True, "refreshed": len(results), "instances": results}

    def _select_targets(self, instance_ids: Optional[Iterable[str]]) -> List[Any]:
        targets = self._pool.list(include_dead=False)
        if instance_ids is None:
            return targets
        wanted = {str(item).strip() for item in instance_ids if str(item).strip()}
        return [instance for instance in targets if instance.instance_id in wanted]

    def _find_instance(self, instance_id: str) -> Optional[Any]:
        for item in self._pool.list(include_dead=True):
            if item.instance_id == instance_id:
                return item
        return None

    def _log_refresh_result(self, instance_id: str, snapshot: CacheMetricsSnapshot, previous_last_success: Optional[int]) -> None:
        payload = snapshot.to_state_snapshot()
        if snapshot.status == "ok":
            if previous_last_success is None:
                self._logger.info("[Proxy][LMCache] first metrics fetch ok instance=%s endpoint=%s payload=%s", instance_id, snapshot.source_endpoint, payload)
            else:
                self._logger.debug("[Proxy][LMCache] metrics fetch ok instance=%s endpoint=%s payload=%s", instance_id, snapshot.source_endpoint, payload)
            return
        if snapshot.status == "partial":
            self._logger.warning("[Proxy][LMCache] partial metrics payload instance=%s endpoint=%s payload=%s", instance_id, snapshot.source_endpoint, payload)
            return
        self._logger.warning(
            "[Proxy][LMCache] metrics fetch failed instance=%s endpoint=%s step=%s detail=%s",
            instance_id,
            snapshot.source_endpoint,
            snapshot.fetch_step,
            snapshot.detail,
        )


def resolve_candidate_urls(instance: Any) -> List[str]:
    meta = getattr(instance, "meta", {}) if hasattr(instance, "meta") else {}
    meta = meta if isinstance(meta, dict) else {}
    urls: List[str] = []

    for key in ("lmcache_metrics_url", "lmcache_debug_url", "lmcache_internal_api_url", "vllm_metrics_url"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            urls.append(_ensure_http_url(value))

    multi_value = meta.get("lmcache_metrics_urls")
    if isinstance(multi_value, list):
        for item in multi_value:
            if isinstance(item, str) and item.strip():
                urls.append(_ensure_http_url(item))

    host = str(meta.get("lmcache_metrics_host") or meta.get("lmcache_internal_api_host") or getattr(instance, "host", "") or "").strip()
    ports: List[int] = []
    for key in ("lmcache_metrics_port", "lmcache_internal_api_port", "lmcache_worker_port"):
        port = _as_int(meta.get(key))
        if port is not None and port > 0:
            ports.append(port)

    if host and ports:
        for port in ports:
            for path in _candidate_paths():
                urls.append(f"http://{host}:{port}{path}")

    base_urls = meta.get("lmcache_metrics_base_urls")
    if isinstance(base_urls, list):
        for base in base_urls:
            if isinstance(base, str) and base.strip():
                for path in _candidate_paths():
                    urls.append(_join_base_and_path(base, path))

    return _dedupe(urls)


def _candidate_paths() -> List[str]:
    raw = os.environ.get("PROXY_LMCACHE_METRICS_CANDIDATE_PATHS", "").strip()
    if not raw:
        return list(_DEFAULT_CANDIDATE_PATHS)
    paths = []
    for item in raw.split(","):
        text = item.strip()
        if not text:
            continue
        if not text.startswith("/"):
            text = f"/{text}"
        paths.append(text)
    return paths or list(_DEFAULT_CANDIDATE_PATHS)


def _join_base_and_path(base: str, path: str) -> str:
    return _ensure_http_url(base).rstrip("/") + path


def _ensure_http_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return text
    if "://" not in text:
        return f"http://{text}"
    return text


def _dedupe(values: Iterable[str]) -> List[str]:
    seen = set()
    ordered = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return ordered


def _parse_prometheus_text(text: str) -> Dict[str, Any]:
    metrics: Dict[str, float] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if " " not in line:
            continue
        metric_name, raw_value = line.rsplit(" ", 1)
        metric_name = metric_name.split("{", 1)[0].strip()
        value = _as_float(raw_value)
        if not metric_name or value is None:
            continue
        metrics[metric_name] = value
    return metrics


def _flatten_payload(payload: Any, prefix: str = "") -> Dict[str, Any]:
    flat: Dict[str, Any] = {}
    if isinstance(payload, dict):
        for key, value in payload.items():
            next_prefix = f"{prefix}.{key}" if prefix else str(key)
            flat.update(_flatten_payload(value, next_prefix))
        return flat
    if isinstance(payload, list):
        for idx, value in enumerate(payload):
            next_prefix = f"{prefix}[{idx}]"
            flat.update(_flatten_payload(value, next_prefix))
        return flat
    if prefix:
        flat[prefix] = payload
    return flat


def _find_metric_value(flat: Dict[str, Any], aliases: Iterable[str]) -> Optional[float]:
    normalized = {(_normalize_key(key), key): value for key, value in flat.items()}
    alias_norms = [_normalize_key(alias) for alias in aliases]

    for alias in alias_norms:
        for (norm_key, _), value in normalized.items():
            if norm_key == alias:
                number = _as_float(value)
                if number is not None:
                    return number

    for alias in alias_norms:
        for (norm_key, _), value in normalized.items():
            if alias in norm_key:
                number = _as_float(value)
                if number is not None:
                    return number
    return None


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")


def _derive_pressure_score(values: Dict[str, Any]) -> Optional[float]:
    scores: List[float] = []
    l1_usage = _as_float(values.get("l1_usage_bytes"))
    l1_capacity = _as_float(values.get("l1_capacity_bytes"))
    l2_usage = _as_float(values.get("l2_usage_bytes"))
    l2_capacity = _as_float(values.get("l2_capacity_bytes"))
    eviction_rate = _as_float(values.get("eviction_rate"))
    if l1_usage is not None and l1_capacity and l1_capacity > 0:
        scores.append(max(0.0, min(1.0, l1_usage / l1_capacity)))
    if l2_usage is not None and l2_capacity and l2_capacity > 0:
        scores.append(max(0.0, min(1.0, l2_usage / l2_capacity)))
    if eviction_rate is not None:
        scores.append(max(0.0, min(1.0, eviction_rate)))
    if not scores:
        return None
    return sum(scores) / len(scores)


def _short_error(step: str, url: str, exc: Exception) -> str:
    return f"{step}:{url}:{type(exc).__name__}:{exc}"


def _cache_metrics_to_public_dict(metrics: Any, instance_id: str) -> Dict[str, Any]:
    return {
        "instance_id": instance_id,
        "status": getattr(metrics, "status", None),
        "source": getattr(metrics, "source", None),
        "source_endpoint": getattr(metrics, "source_endpoint", None),
        "timestamp": getattr(metrics, "timestamp", None),
        "last_reported_at": getattr(metrics, "last_reported_at", None),
        "last_success_at": getattr(metrics, "last_success_at", None),
        "last_error_at": getattr(metrics, "last_error_at", None),
        "success_count": getattr(metrics, "success_count", None),
        "error_count": getattr(metrics, "error_count", None),
        "last_error": getattr(metrics, "last_error", None),
        "detail": getattr(metrics, "detail", None),
        "fetch_step": getattr(metrics, "fetch_step", None),
        "hit_rate": getattr(metrics, "hit_rate", None),
        "lookup_qps": getattr(metrics, "lookup_qps", None),
        "store_qps": getattr(metrics, "store_qps", None),
        "retrieve_qps": getattr(metrics, "retrieve_qps", None),
        "eviction_rate": getattr(metrics, "eviction_rate", None),
        "avg_lookup_latency_ms": getattr(metrics, "avg_lookup_latency_ms", None),
        "avg_store_latency_ms": getattr(metrics, "avg_store_latency_ms", None),
        "avg_retrieve_latency_ms": getattr(metrics, "avg_retrieve_latency_ms", None),
        "l1_usage_bytes": getattr(metrics, "l1_usage_bytes", None),
        "l1_capacity_bytes": getattr(metrics, "l1_capacity_bytes", None),
        "l2_usage_bytes": getattr(metrics, "l2_usage_bytes", None),
        "l2_capacity_bytes": getattr(metrics, "l2_capacity_bytes", None),
        "active_sessions": getattr(metrics, "active_sessions", None),
        "pressure_score": getattr(metrics, "pressure_score", None),
    }


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            value = text
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    try:
        number = _as_float(value)
        return int(number) if number is not None else None
    except (TypeError, ValueError):
        return None
