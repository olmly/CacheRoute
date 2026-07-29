"""In-memory cache visibility index for Proxy."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from .event_adapter import CacheDomainEvent

"""某一个具体的缓存块（chunk_key）在某一时刻的精确位置和状态。
"""
@dataclass
class ChunkLocationRecord:
    namespace: str
    chunk_key: str
    instance_id: str
    device: str
    state: str
    first_seen_at: int
    last_seen_at: int

"""某一个实例（instance_id）在某个命名空间（namespace）下的整体健康状态。
"""
@dataclass
class InstanceNamespaceSummary:
    namespace: str
    instance_id: str
    chunk_count: int = 0
    last_event_at: Optional[int] = None  #最后事件事件
    last_event_type: Optional[str] = None  #最后事件类型


class CacheVisibilityIndex:
    """维护 Proxy 本地内存可见性索引，严格区分写路径和读路径。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chunk_locations: Dict[str, Dict[str, Dict[str, ChunkLocationRecord]]] = {}
        self._instance_summary: Dict[str, Dict[str, InstanceNamespaceSummary]] = {}
        self._prefix_chunks: Dict[str, Dict[str, Tuple[str, ...]]] = {}
        self._stats: Dict[str, Any] = {
            "event_count": 0,
            "stored_events": 0,
            "evicted_events": 0,
            "cleared_events": 0,
            "skipped_events": 0,
            "last_event_at": None,
        }

    def apply_event(self, event: CacheDomainEvent) -> Dict[str, Any]:
        """写路径入口：订阅器/适配器产出的事件统一从这里进入索引。"""
        with self._lock:
            self._stats["event_count"] = int(self._stats.get("event_count", 0) or 0) + 1
            self._stats["last_event_at"] = event.occurred_at

            if event.event_type == "block_stored":
                self._apply_block_stored(event)
                self._stats["stored_events"] = int(self._stats.get("stored_events", 0) or 0) + 1
            elif event.event_type == "block_evicted":
                self._apply_block_evicted(event)
                self._stats["evicted_events"] = int(self._stats.get("evicted_events", 0) or 0) + 1
            elif event.event_type == "all_blocks_cleared":
                self._apply_all_blocks_cleared(event)
                self._stats["cleared_events"] = int(self._stats.get("cleared_events", 0) or 0) + 1
            elif event.event_type == "instance_registered":
                self._touch_instance_summary(event)
            elif event.event_type == "instance_unregistered":
                self._remove_instance_records(event.namespace, event.instance_id)
            else:
                self._stats["skipped_events"] = int(self._stats.get("skipped_events", 0) or 0) + 1

            if event.prefix_key and event.chunk_keys:
                self._prefix_chunks.setdefault(event.namespace, {})[event.prefix_key] = tuple(event.chunk_keys)

            summary = self._instance_summary.setdefault(event.namespace, {}).setdefault(
                event.instance_id,
                InstanceNamespaceSummary(namespace=event.namespace, instance_id=event.instance_id),
            )
            summary.last_event_at = event.occurred_at
            summary.last_event_type = event.event_type
            summary.chunk_count = self._count_instance_chunks_locked(event.namespace, event.instance_id)

            return {
                "namespace": event.namespace,
                "instance_id": event.instance_id,
                "event_type": event.event_type,
                "chunk_count": summary.chunk_count,
                "chunk_key": event.chunk_key,
            }

    def get_chunk_locations(self, namespace: str, chunk_key: str) -> List[Dict[str, Any]]:
        with self._lock:
            namespace_map = self._chunk_locations.get(namespace, {})
            instance_map = namespace_map.get(chunk_key, {})
            rows = list(instance_map.values())
        return [
            {
                "namespace": row.namespace,
                "chunk_key": row.chunk_key,
                "instance_id": row.instance_id,
                "device": row.device,
                "state": row.state,
                "first_seen_at": row.first_seen_at,
                "last_seen_at": row.last_seen_at,
            }
            for row in sorted(rows, key=lambda item: (-int(item.last_seen_at), item.instance_id))
        ]

    def get_instance_summary(self, instance_id: str, namespace: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._lock:
            namespaces = [namespace] if namespace else list(self._instance_summary.keys())
            rows: List[InstanceNamespaceSummary] = []
            for ns in namespaces:
                summary = self._instance_summary.get(ns or "", {}).get(instance_id)
                if summary is not None:
                    rows.append(summary)
        return [
            {
                "namespace": row.namespace,
                "instance_id": row.instance_id,
                "chunk_count": row.chunk_count,
                "last_event_at": row.last_event_at,
                "last_event_type": row.last_event_type,
            }
            for row in sorted(rows, key=lambda item: item.namespace)
        ]

    def get_namespace_summary(self, namespace: str) -> Dict[str, Any]:
        with self._lock:
            instance_rows = list(self._instance_summary.get(namespace, {}).values())
            chunk_count = len(self._chunk_locations.get(namespace, {}))
            prefix_count = len(self._prefix_chunks.get(namespace, {}))
        return {
            "namespace": namespace,
            "chunk_count": chunk_count,
            "prefix_count": prefix_count,
            "instance_count": len(instance_rows),
            "instances": [
                {
                    "instance_id": row.instance_id,
                    "chunk_count": row.chunk_count,
                    "last_event_at": row.last_event_at,
                    "last_event_type": row.last_event_type,
                }
                for row in sorted(instance_rows, key=lambda item: item.instance_id)
            ],
        }

    def get_index_stats(self) -> Dict[str, Any]:
        with self._lock:
            namespace_count = len(self._chunk_locations)
            chunk_total = sum(len(chunk_map) for chunk_map in self._chunk_locations.values())
            instance_total = sum(len(summary) for summary in self._instance_summary.values())
            prefix_total = sum(len(prefixes) for prefixes in self._prefix_chunks.values())
            stats = dict(self._stats)
        stats.update(
            {
                "namespace_count": namespace_count,
                "chunk_total": chunk_total,
                "instance_total": instance_total,
                "prefix_total": prefix_total,
                "generated_at": time.time(),
            }
        )
        return stats

    def lookup_prefix(self, namespace: str, prefix_key: str) -> Dict[str, Any]:
        with self._lock:
            chunk_keys = list(self._prefix_chunks.get(namespace, {}).get(prefix_key, ()))
            if not chunk_keys:
                return {"namespace": namespace, "prefix_key": prefix_key, "chunk_keys": [], "matches": []}

            per_instance: Dict[str, Dict[str, Any]] = {}
            for chunk_key in chunk_keys:
                for row in self._chunk_locations.get(namespace, {}).get(chunk_key, {}).values():
                    match = per_instance.setdefault(
                        row.instance_id,
                        {"instance_id": row.instance_id, "matched_chunks": 0, "total_chunks": len(chunk_keys), "devices": set(), "last_seen_at": 0},
                    )
                    match["matched_chunks"] += 1
                    match["devices"].add(row.device)
                    match["last_seen_at"] = max(int(match["last_seen_at"]), int(row.last_seen_at))

        matches = []
        for item in per_instance.values():
            total_chunks = int(item["total_chunks"] or 0)
            matched_chunks = int(item["matched_chunks"] or 0)
            matches.append(
                {
                    "instance_id": item["instance_id"],
                    "matched_chunks": matched_chunks,
                    "total_chunks": total_chunks,
                    "residency_score": (float(matched_chunks) / float(total_chunks)) if total_chunks > 0 else 0.0,
                    "devices": sorted(str(device) for device in item["devices"]),
                    "last_seen_at": item["last_seen_at"],
                }
            )
        matches.sort(key=lambda row: (-float(row["residency_score"]), -int(row["last_seen_at"])))
        return {"namespace": namespace, "prefix_key": prefix_key, "chunk_keys": chunk_keys, "matches": matches}

    def _apply_block_stored(self, event: CacheDomainEvent) -> None:
        if not event.chunk_key:
            return
        namespace_map = self._chunk_locations.setdefault(event.namespace, {})
        instance_map = namespace_map.setdefault(event.chunk_key, {})
        existing = instance_map.get(event.instance_id)
        if existing is None:
            instance_map[event.instance_id] = ChunkLocationRecord(
                namespace=event.namespace,
                chunk_key=event.chunk_key,
                instance_id=event.instance_id,
                device=event.device,
                state=event.state,
                first_seen_at=event.occurred_at,
                last_seen_at=event.occurred_at,
            )
        else:
            existing.device = event.device
            existing.state = event.state
            existing.last_seen_at = event.occurred_at

    def _apply_block_evicted(self, event: CacheDomainEvent) -> None:
        if not event.chunk_key:
            return
        namespace_map = self._chunk_locations.get(event.namespace, {})
        instance_map = namespace_map.get(event.chunk_key, {})
        row = instance_map.get(event.instance_id)
        if row is not None:
            row.state = event.state
            row.last_seen_at = event.occurred_at
            # 中文注释：第一阶段把 evicted 事件视作“从可见索引中移除”，这样查询结果更接近当前可用状态。
            instance_map.pop(event.instance_id, None)
            if not instance_map:
                namespace_map.pop(event.chunk_key, None)

    def _apply_all_blocks_cleared(self, event: CacheDomainEvent) -> None:
        namespace_map = self._chunk_locations.get(event.namespace, {})
        for chunk_key in list(namespace_map.keys()):
            namespace_map[chunk_key].pop(event.instance_id, None)
            if not namespace_map[chunk_key]:
                namespace_map.pop(chunk_key, None)
        prefix_map = self._prefix_chunks.get(event.namespace, {})
        if prefix_map:
            self._prefix_chunks[event.namespace] = {
                prefix_key: chunk_keys
                for prefix_key, chunk_keys in prefix_map.items()
                if any(self._chunk_locations.get(event.namespace, {}).get(chunk_key) for chunk_key in chunk_keys)
            }

    def _touch_instance_summary(self, event: CacheDomainEvent) -> None:
        summary = self._instance_summary.setdefault(event.namespace, {}).setdefault(
            event.instance_id,
            InstanceNamespaceSummary(namespace=event.namespace, instance_id=event.instance_id),
        )
        summary.last_event_at = event.occurred_at
        summary.last_event_type = event.event_type
        summary.chunk_count = self._count_instance_chunks_locked(event.namespace, event.instance_id)

    def _remove_instance_records(self, namespace: str, instance_id: str) -> None:
        namespace_map = self._chunk_locations.get(namespace, {})
        for chunk_key in list(namespace_map.keys()):
            namespace_map[chunk_key].pop(instance_id, None)
            if not namespace_map[chunk_key]:
                namespace_map.pop(chunk_key, None)
        self._instance_summary.get(namespace, {}).pop(instance_id, None)

    def _count_instance_chunks_locked(self, namespace: str, instance_id: str) -> int:
        namespace_map = self._chunk_locations.get(namespace, {})
        total = 0
        for instance_map in namespace_map.values():
            if instance_id in instance_map:
                total += 1
        return total
