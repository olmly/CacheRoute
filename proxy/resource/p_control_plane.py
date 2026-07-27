# proxy/resource/p_control_plane.py

"""Exposes the proxy control plane for instance registration, heartbeat, and topology metadata."""
from __future__ import annotations

import asyncio
import inspect
import os
import time
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from .instance_pool import InstancePool

import logging
logger = logging.getLogger("proxy.p_control_plane")


_control_plane = FastAPI(title="CacheRoute Proxy Control Plane", version="v1")
_pool: Optional[InstancePool] = None
_kdn_links: Dict[str, Dict[str, Any]] = {}
_instance_kdn_links: Dict[str, Dict[str, Dict[str, Any]]] = {}
_kdn_links_lock = asyncio.Lock()
_resource_snapshot_seen: set[str] = set()
_unknown_resource_warn_at: Dict[str, float] = {}
_proxy_id: str = os.environ.get("PROXY_ID", "unknown")
_proxy_capacity: int = int(os.environ.get("PROXY_MAX_CAPACITY", "0") or 0)
_queue_snapshot_provider: Optional[Callable[[], Dict[str, Any]]] = None
_cache_refresh_provider: Optional[Callable[[Optional[List[str]]], Any]] = None


def set_pool(pool: InstancePool) -> None:
    global _pool
    _pool = pool


def set_pool_resource_context(proxy_id: str, capacity: int = 0) -> None:
    global _proxy_id, _proxy_capacity
    _proxy_id = proxy_id
    _proxy_capacity = int(capacity or 0)


def set_queue_snapshot_provider(provider: Optional[Callable[[], Dict[str, Any]]]) -> None:
    """Register a lazy queue-depth provider without coupling control plane to QueueManager."""
    global _queue_snapshot_provider
    _queue_snapshot_provider = provider


def set_cache_refresh_provider(provider: Optional[Callable[[Optional[List[str]]], Any]]) -> None:
    """Register an optional cache refresh hook owned by the Proxy runtime."""
    global _cache_refresh_provider
    _cache_refresh_provider = provider


def _build_pool_resource_snapshot() -> Dict[str, Any]:
    pool = get_pool()
    prepare_queue_depth = None
    ready_queue_depth = None
    if _queue_snapshot_provider is not None:
        try:
            queue_depths = _queue_snapshot_provider() or {}
            prepare_queue_depth = queue_depths.get("prepare_queue_depth")
            ready_queue_depth = queue_depths.get("ready_queue_depth")
        except Exception:
            logger.warning(
                "[ProxyCP] queue snapshot provider failed; queue metrics unavailable",
                exc_info=True,
            )
    return pool.build_pool_resource_snapshot(
        proxy_id=_proxy_id,
        capacity=_proxy_capacity,
        prepare_queue_depth=prepare_queue_depth,
        ready_queue_depth=ready_queue_depth,
    )


def get_pool() -> InstancePool:
    if _pool is None:
        raise RuntimeError("InstancePool is not set. Call set_pool() in proxy startup.")
    return _pool


class InstanceRegisterReq(BaseModel):
    instance_id: Optional[str] = None
    host: str
    port: int
    endpoints: List[str] = []
    tags: List[str] = []
    weight: float = 1.0
    meta: Dict[str, Any] = {}


class InstanceHeartbeatReq(BaseModel):
    instance_id: str
    inflight: Optional[int] = None
    qps_1m: Optional[float] = None
    gpu_util: Optional[float] = None


class InstanceUnregisterReq(BaseModel):
    instance_id: str


class InstanceResourceSnapshotReq(BaseModel):
    instance_id: str
    snapshot: Dict[str, Any]
    metadata: Dict[str, Any] = {}


class InstanceCacheMetricsReq(BaseModel):
    instance_id: str
    snapshot: Dict[str, Any]
    metadata: Dict[str, Any] = {}


class InstanceCacheDirectoryReq(BaseModel):
    instance_id: str
    prefix_key: str
    chunk_keys: List[str]
    token_count: Optional[int] = None
    matched_tokens: Optional[int] = None
    device: str = "unknown"
    state: str = "present"
    last_validated_at: Optional[int] = None


class TopologyReportReq(BaseModel):
    instance_id: str
    links: Dict[str, Dict[str, Any]]


class CacheRefreshReq(BaseModel):
    instance_ids: Optional[List[str]] = None


async def get_kdn_links_snapshot() -> Dict[str, Dict[str, Any]]:
    async with _kdn_links_lock:
        return {k: dict(v) for k, v in _kdn_links.items()}


def _is_better_link(new_item: Dict[str, Any], old_item: Dict[str, Any]) -> bool:
    """
    Compare two Instance->KDN links and return whether new_item is better.
    Rule: prefer higher bandwidth; when bandwidth ties, prefer lower latency.
    """
    new_bw = float(new_item.get("bandwidth_mbps", 0.0) or 0.0)
    old_bw = float(old_item.get("bandwidth_mbps", 0.0) or 0.0)
    if new_bw != old_bw:
        return new_bw > old_bw
    new_lat = float(new_item.get("latency_ms", 1e9) or 1e9)
    old_lat = float(old_item.get("latency_ms", 1e9) or 1e9)
    return new_lat < old_lat


def _rebuild_best_kdn_links_locked() -> None:
    best: Dict[str, Dict[str, Any]] = {}
    for instance_id, links in _instance_kdn_links.items():
        for kdn_key, metrics in (links or {}).items():
            if not isinstance(metrics, dict):
                continue
            current = best.get(kdn_key)
            if current is None or _is_better_link(metrics, current):
                item = dict(metrics)
                item["reported_by"] = instance_id
                best[kdn_key] = item
    _kdn_links.clear()
    _kdn_links.update(best)


@_control_plane.get("/healthz")
async def healthz() -> Dict[str, Any]:
    pool = get_pool()
    return {"ok": True, "ttl_s": pool.ttl_s}




@_control_plane.get("/debug/status")
async def debug_status() -> Dict[str, Any]:
    pool = get_pool()
    alive_items = pool.list(include_dead=False)
    all_items = pool.list(include_dead=True)
    return {
        "ok": True,
        "ttl_s": pool.ttl_s,
        "alive_instances": len(alive_items),
        "total_instances": len(all_items),
        "expired_instances": max(0, len(all_items) - len(alive_items)),
        "sample_ids": [it.instance_id for it in alive_items[:10]],
        "topology_kdn_links": len(_kdn_links),
    }


@_control_plane.post("/v1/instance/register")
async def register(req: InstanceRegisterReq) -> Dict[str, Any]:
    pool = get_pool()
    instance_id = req.instance_id or f"hp_{req.host}:{req.port}"
    it = pool.upsert(
        instance_id=instance_id,
        host=req.host,
        port=req.port,
        endpoints=req.endpoints,
        tags=req.tags,
        weight=req.weight,
        meta=req.meta,
    )

    logger.info(
        "[ProxyCP] instance register: id=%s addr=%s:%s endpoints=%s tags=%s weight=%s meta=%s",
        it.instance_id, it.host, it.port, it.endpoints, it.tags, it.weight, it.meta
    )

    # Suggest an instance heartbeat interval: fixed 10s or ttl/3, whichever is smaller
    hb = min(10, max(1, pool.ttl_s // 3))
    return {
        "instance_id": it.instance_id,
        "heartbeat_interval_s": hb,
        "ttl_s": pool.ttl_s,
    }


@_control_plane.post("/v1/instance/heartbeat")
async def heartbeat(req: InstanceHeartbeatReq) -> Dict[str, Any]:
    pool = get_pool()
    ok = pool.heartbeat(
        instance_id=req.instance_id,
        inflight=req.inflight,
        qps_1m=req.qps_1m,
        gpu_util=req.gpu_util,
    )
    if not ok:
        logger.warning("[ProxyCP] heartbeat for unknown instance_id=%s", req.instance_id)

    return {"ok": ok}


@_control_plane.post("/v1/instance/resource_snapshot")
async def report_resource_snapshot(req: InstanceResourceSnapshotReq) -> Dict[str, Any]:
    pool = get_pool()
    ok = pool.report_resource_snapshot(
        instance_id=req.instance_id,
        snapshot=req.snapshot,
        metadata=req.metadata,
    )
    if not ok:
        now = asyncio.get_running_loop().time()
        last = _unknown_resource_warn_at.get(req.instance_id, 0.0)
        if now - last >= 30.0:
            _unknown_resource_warn_at[req.instance_id] = now
            logger.warning("[ProxyCP] resource snapshot for unknown instance_id=%s", req.instance_id)
        return {"ok": False, "error": "unknown_instance"}
    if req.instance_id not in _resource_snapshot_seen:
        _resource_snapshot_seen.add(req.instance_id)
        logger.info("[ProxyCP] first resource snapshot updated: instance_id=%s", req.instance_id)
    else:
        logger.debug("[ProxyCP] resource snapshot updated: instance_id=%s", req.instance_id)
    return {"ok": True}


@_control_plane.post("/v1/instance/cache_metrics")
async def report_cache_metrics(req: InstanceCacheMetricsReq) -> Dict[str, Any]:
    pool = get_pool()
    ok = pool.report_cache_metrics(
        instance_id=req.instance_id,
        snapshot=req.snapshot,
        metadata=req.metadata,
    )
    if not ok:
        logger.warning("[ProxyCP] cache metrics for unknown instance_id=%s", req.instance_id)
        return {"ok": False, "error": "unknown_instance"}
    logger.debug("[ProxyCP] cache metrics updated: instance_id=%s", req.instance_id)
    return {"ok": True}


@_control_plane.post("/v1/instance/cache_directory")
async def report_cache_directory(req: InstanceCacheDirectoryReq) -> Dict[str, Any]:
    pool = get_pool()
    ok = pool.report_cache_directory(
        instance_id=req.instance_id,
        prefix_key=req.prefix_key,
        chunk_keys=req.chunk_keys,
        token_count=req.token_count,
        matched_tokens=req.matched_tokens,
        device=req.device,
        state=req.state,
        last_validated_at=req.last_validated_at,
    )
    if not ok:
        logger.warning(
            "[ProxyCP] cache directory update rejected: instance_id=%s prefix_key=%s chunks=%s",
            req.instance_id,
            req.prefix_key,
            len(req.chunk_keys or []),
        )
        return {"ok": False, "error": "invalid_update_or_unknown_instance"}
    return {"ok": True}


@_control_plane.post("/v1/instance/unregister")
async def unregister(req: InstanceUnregisterReq) -> Dict[str, Any]:
    pool = get_pool()
    ok = pool.remove(req.instance_id)
    async with _kdn_links_lock:
        _instance_kdn_links.pop(req.instance_id, None)
        _rebuild_best_kdn_links_locked()
    logger.info("[ProxyCP] instance unregister: id=%s ok=%s", req.instance_id, ok)
    return {"ok": ok}


@_control_plane.get("/v1/instance/list")
async def list_instances(include_dead: bool = False) -> List[Dict[str, Any]]:
    pool = get_pool()
    items = pool.list(include_dead=include_dead)
    out: List[Dict[str, Any]] = []
    now = int(time.time())
    for it in items:
        is_alive = (now - int(it.last_seen_at)) <= pool.ttl_s
        out.append({
            "instance_id": it.instance_id,
            "host": it.host,
            "port": it.port,
            "endpoints": it.endpoints,
            "tags": it.tags,
            "weight": it.weight,
            "meta": it.meta,
            "registered_at": it.registered_at,
            "last_seen_at": it.last_seen_at,
            "load": {
                "inflight": it.load.inflight,
                "qps_1m": it.load.qps_1m,
                "gpu_util": it.load.gpu_util,
            },
            "cache_metrics": {
                "status": it.cache_metrics.status,
                "source": it.cache_metrics.source,
                "source_endpoint": it.cache_metrics.source_endpoint,
                "timestamp": it.cache_metrics.timestamp,
                "last_reported_at": it.cache_metrics.last_reported_at,
                "last_success_at": it.cache_metrics.last_success_at,
                "last_error_at": it.cache_metrics.last_error_at,
                "success_count": it.cache_metrics.success_count,
                "error_count": it.cache_metrics.error_count,
                "last_error": it.cache_metrics.last_error,
                "fetch_step": it.cache_metrics.fetch_step,
                "detail": it.cache_metrics.detail,
                "hit_rate": it.cache_metrics.hit_rate,
                "eviction_rate": it.cache_metrics.eviction_rate,
                "store_qps": it.cache_metrics.store_qps,
                "retrieve_qps": it.cache_metrics.retrieve_qps,
                "lookup_qps": it.cache_metrics.lookup_qps,
                "avg_store_latency_ms": it.cache_metrics.avg_store_latency_ms,
                "avg_retrieve_latency_ms": it.cache_metrics.avg_retrieve_latency_ms,
                "avg_lookup_latency_ms": it.cache_metrics.avg_lookup_latency_ms,
                "l1_usage_bytes": it.cache_metrics.l1_usage_bytes,
                "l1_capacity_bytes": it.cache_metrics.l1_capacity_bytes,
                "l2_usage_bytes": it.cache_metrics.l2_usage_bytes,
                "l2_capacity_bytes": it.cache_metrics.l2_capacity_bytes,
                "active_sessions": it.cache_metrics.active_sessions,
                "pressure_score": it.cache_metrics.pressure_score,
            },
            "resource": {
                "cpu_util": it.resource.cpu_util,
                "memory_used_mb": it.resource.memory_used_mb,
                "memory_total_mb": it.resource.memory_total_mb,
                "memory_free_mb": it.resource.memory_free_mb,
                "memory_free_ratio": it.resource.memory_free_ratio,
                "gpu_util_avg": it.resource.gpu_util_avg,
                "gpu_util_current": it.resource.gpu_util_current,
                "gpu_util_max": it.resource.gpu_util_max,
                "gpu_sample_count": it.resource.gpu_sample_count,
                "gpu_sample_age_ms": it.resource.gpu_sample_age_ms,
                "gpu_sample_source": it.resource.gpu_sample_source,
                "gpu_sample_quality": it.resource.gpu_sample_quality,
                "gpu_sample_window_ms": it.resource.gpu_sample_window_ms,
                "gpu_mem_used_mb": it.resource.gpu_mem_used_mb,
                "gpu_mem_total_mb": it.resource.gpu_mem_total_mb,
                "network_rx_mbps": it.resource.network_rx_mbps,
                "network_tx_mbps": it.resource.network_tx_mbps,
                "admission_state": it.resource.admission_state,
                "resource_ts_ms": it.resource.resource_ts_ms,
                "resource_reported_at": it.resource.resource_reported_at,
                "resource_report_monotonic_ms": it.resource.resource_report_monotonic_ms,
                "resource_report_wall_time_ms": it.resource.resource_report_wall_time_ms,
                "reported_instance_id": it.resource.reported_instance_id,
                "raw_resource": it.resource.raw_resource,
            },
            # Always expose the real TTL-derived state so UIs can distinguish alive and stale rows.
            "is_alive": is_alive,
        })
    return out




@_control_plane.get("/debug/pool_resource")
async def debug_pool_resource() -> Dict[str, Any]:
    return {"ok": True, "pool_resource": _build_pool_resource_snapshot()}


@_control_plane.get("/debug/pool_resource_sources")
async def debug_pool_resource_sources() -> Dict[str, Any]:
    snapshot = _build_pool_resource_snapshot()
    return {
        "ok": True,
        "metric_source": snapshot.get("metric_source", {}),
        "metric_quality": snapshot.get("metric_quality", {}),
        "null_semantics": "0 means measured zero; null means unavailable, not wired, or unknown",
    }


@_control_plane.get("/debug/instance_resources")
async def debug_instance_resources(include_dead: bool = True) -> Dict[str, Any]:
    pool = get_pool()
    items = pool.list(include_dead=include_dead)
    resources: List[Dict[str, Any]] = []
    for it in items:
        resources.append({
            "instance_id": it.instance_id,
            "host": it.host,
            "port": it.port,
            "last_seen_at": it.last_seen_at,
            "resource": {
                "cpu_util": it.resource.cpu_util,
                "memory_used_mb": it.resource.memory_used_mb,
                "memory_total_mb": it.resource.memory_total_mb,
                "memory_free_mb": it.resource.memory_free_mb,
                "memory_free_ratio": it.resource.memory_free_ratio,
                "gpu_util_avg": it.resource.gpu_util_avg,
                "gpu_util_current": it.resource.gpu_util_current,
                "gpu_util_max": it.resource.gpu_util_max,
                "gpu_sample_count": it.resource.gpu_sample_count,
                "gpu_sample_age_ms": it.resource.gpu_sample_age_ms,
                "gpu_sample_source": it.resource.gpu_sample_source,
                "gpu_sample_quality": it.resource.gpu_sample_quality,
                "gpu_sample_window_ms": it.resource.gpu_sample_window_ms,
                "gpu_mem_used_mb": it.resource.gpu_mem_used_mb,
                "gpu_mem_total_mb": it.resource.gpu_mem_total_mb,
                "network_rx_mbps": it.resource.network_rx_mbps,
                "network_tx_mbps": it.resource.network_tx_mbps,
                "admission_state": it.resource.admission_state,
                "resource_ts_ms": it.resource.resource_ts_ms,
                "resource_reported_at": it.resource.resource_reported_at,
                "resource_report_monotonic_ms": it.resource.resource_report_monotonic_ms,
                "resource_report_wall_time_ms": it.resource.resource_report_wall_time_ms,
                "reported_instance_id": it.resource.reported_instance_id,
            },
        })
    return {"instances": resources}


@_control_plane.get("/debug/instance_loads")
async def debug_instance_loads(include_dead: bool = True) -> Dict[str, Any]:
    pool = get_pool()
    queue_depths = None
    if _queue_snapshot_provider is not None:
        try:
            queue_depths = _queue_snapshot_provider() or {}
        except Exception:
            logger.warning(
                "[ProxyCP] queue snapshot provider failed; instance load queue metrics unavailable",
                exc_info=True,
            )
    snapshot = pool.snapshot_instance_loads(queue_depths=queue_depths, include_dead=include_dead)
    return {"ok": True, **snapshot}


@_control_plane.get("/debug/cache_metrics")
async def debug_cache_metrics(include_dead: bool = True) -> Dict[str, Any]:
    pool = get_pool()
    snapshot = pool.snapshot_cache_metrics(include_dead=include_dead)
    return {"ok": True, **snapshot}


@_control_plane.get("/debug/cache/instances")
async def debug_cache_instances(include_dead: bool = True) -> Dict[str, Any]:
    pool = get_pool()
    snapshot = pool.snapshot_cache_instances(include_dead=include_dead)
    return {"ok": True, **snapshot}


@_control_plane.get("/debug/cache/summary")
async def debug_cache_summary(include_dead: bool = True) -> Dict[str, Any]:
    pool = get_pool()
    snapshot = pool.snapshot_cache_summary(include_dead=include_dead)
    return {"ok": True, **snapshot}


@_control_plane.post("/debug/cache/refresh")
async def debug_cache_refresh(req: CacheRefreshReq) -> Dict[str, Any]:
    if _cache_refresh_provider is None:
        return {"ok": False, "error": "cache_refresh_unavailable"}
    result = _cache_refresh_provider(req.instance_ids)
    if inspect.isawaitable(result):
        result = await result
    return result if isinstance(result, dict) else {"ok": True, "result": result}


@_control_plane.get("/debug/cache_directory")
async def debug_cache_directory(include_dead: bool = True) -> Dict[str, Any]:
    pool = get_pool()
    snapshot = pool.snapshot_cache_directory(include_dead=include_dead)
    return {"ok": True, **snapshot}


@_control_plane.get("/debug/cache_prefix/{prefix_key}")
async def debug_cache_prefix(prefix_key: str, include_dead: bool = True) -> Dict[str, Any]:
    pool = get_pool()
    matches = pool.lookup_prefix(prefix_key, include_dead=include_dead)
    return {
        "ok": True,
        "prefix_key": prefix_key,
        "matches": [
            {
                "instance_id": item.instance_id,
                "matched_chunks": item.matched_chunks,
                "total_chunks": item.total_chunks,
                "matched_tokens": item.matched_tokens,
                "residency_score": item.residency_score,
                "devices": item.devices,
                "last_seen_at": item.last_seen_at,
            }
            for item in matches
        ],
    }


@_control_plane.post("/v1/topology/report")
async def report_topology(req: TopologyReportReq) -> Dict[str, Any]:
    merged = 0
    async with _kdn_links_lock:
        sanitized: Dict[str, Dict[str, Any]] = {}
        for kdn_key, metrics in (req.links or {}).items():
            if not isinstance(metrics, dict):
                continue
            sanitized[str(kdn_key)] = dict(metrics)
        _instance_kdn_links[req.instance_id] = sanitized
        _rebuild_best_kdn_links_locked()
        merged = len(sanitized)
    logger.info("[ProxyCP] topology report: instance_id=%s links=%s", req.instance_id, merged)
    return {"ok": True, "merged_links": merged}


@_control_plane.get("/v1/topology/kdn_links")
async def list_topology_links() -> Dict[str, Any]:
    return {"kdn_links": await get_kdn_links_snapshot()}


# Export app publicly
control_plane = _control_plane
