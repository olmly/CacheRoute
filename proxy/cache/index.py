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


@dataclass
class PhysicalChunkLocationRecord:
    """Exact LMCache ObjectKey replica kept for debug visibility."""

    physical_key: str
    instance_id: str
    device: str
    state: str
    kv_rank: int
    object_group_id: int
    cache_salt: Optional[str]
    event_keys_count: Optional[int]
    first_seen_at: int
    last_seen_at: int


class CacheVisibilityIndex:
    """维护 Proxy 本地内存可见性索引，严格区分写路径和读路径。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._chunk_locations: Dict[str, Dict[str, Dict[str, ChunkLocationRecord]]] = {}
        self._physical_chunk_locations: Dict[str, Dict[str, Dict[str, PhysicalChunkLocationRecord]]] = {}
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
            elif event.event_type == "block_read":
                self.touch_block(
                    namespace=event.namespace,
                    chunk_key=str(event.chunk_key or ""),
                    instance_id=event.instance_id,
                    occurred_at=event.occurred_at,
                    device=event.device,
                    state=event.state,
                )
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

    def list_chunks(
        self,
        namespace: Optional[str] = None,
        instance_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the currently visible chunk keys and all of their locations."""
        with self._lock:
            namespaces = [namespace] if namespace is not None else sorted(self._chunk_locations)
            chunks: List[Dict[str, Any]] = []
            for current_namespace in namespaces:
                namespace_map = self._chunk_locations.get(current_namespace, {})
                for chunk_key in sorted(namespace_map):
                    locations = list(namespace_map[chunk_key].values())
                    if instance_id is not None:
                        locations = [row for row in locations if row.instance_id == instance_id]
                    if not locations:
                        continue
                    physical_locations = self._list_physical_locations_locked(
                        current_namespace,
                        chunk_key,
                        instance_id,
                    )
                    chunks.append(
                        {
                            "namespace": current_namespace,
                            "chunk_key": chunk_key,
                            "locations": [
                                {
                                    "instance_id": row.instance_id,
                                    "device": row.device,
                                    "state": row.state,
                                    "first_seen_at": row.first_seen_at,
                                    "last_seen_at": row.last_seen_at,
                                }
                                for row in sorted(
                                    locations,
                                    key=lambda item: (-int(item.last_seen_at), item.instance_id),
                                )
                            ],
                            "physical_chunk_count": len(physical_locations),
                            "physical_locations": physical_locations,
                        }
                    )
        return chunks

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

    def remove_instance(self, instance_id: str) -> int:
        """Forget every logical and physical cache record for a restarted instance."""
        with self._lock:
            affected = 0
            for namespace in list(self._chunk_locations):
                before = self._count_instance_chunks_locked(namespace, instance_id)
                self._remove_instance_records(namespace, instance_id)
                affected += before
            return affected

    def lookup_prefix(self, namespace: str, prefix_key: str) -> Dict[str, Any]:
        with self._lock:
            chunk_keys = list(self._prefix_chunks.get(namespace, {}).get(prefix_key, ()))
        return self.lookup_longest_prefix(namespace=namespace, chunk_keys=chunk_keys, prefix_key=prefix_key)

    def lookup_longest_prefix(
        self,
        namespace: str,
        chunk_keys: List[str] | Tuple[str, ...],
        prefix_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        normalized_chunk_keys = [str(chunk_key).strip() for chunk_key in (chunk_keys or []) if str(chunk_key).strip()]
        if not normalized_chunk_keys:
            return {
                "namespace": namespace,
                "prefix_key": prefix_key,
                "chunk_keys": [],
                "matches": [],
                "match_strategy": "longest_consecutive_prefix",
            }

        with self._lock:
            namespace_map = self._chunk_locations.get(namespace, {})
            active_instances: Optional[set[str]] = None
            per_instance: Dict[str, Dict[str, Any]] = {}

            for chunk_key in normalized_chunk_keys:
                current_rows = namespace_map.get(chunk_key, {})
                current_instances = set(current_rows.keys())
                if active_instances is None:
                    active_instances = set(current_instances)
                else:
                    active_instances.intersection_update(current_instances)

                if not active_instances:
                    break

                for instance_id in tuple(active_instances):
                    row = current_rows.get(instance_id)
                    if row is None:
                        continue
                    match = per_instance.setdefault(
                        instance_id,
                        {
                            "instance_id": instance_id,
                            "matched_chunks": 0,
                            "total_chunks": len(normalized_chunk_keys),
                            "devices": set(),
                            "last_seen_at": 0,
                        },
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
        matches.sort(key=lambda row: (-int(row["matched_chunks"]), -float(row["residency_score"]), -int(row["last_seen_at"])))
        return {
            "namespace": namespace,
            "prefix_key": prefix_key,
            "chunk_keys": normalized_chunk_keys,
            "matches": matches,
            "match_strategy": "longest_consecutive_prefix",
        }

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
        self._upsert_physical_location_locked(event)

    def touch_block(self, namespace: str, chunk_key: str, instance_id: str, occurred_at: int, device: Optional[str] = None, state: Optional[str] = None) -> bool:
        with self._lock:
            row = self._chunk_locations.get(namespace, {}).get(chunk_key, {}).get(instance_id)
            if row is None:
                return False
            if device:
                row.device = str(device)
            if state:
                row.state = str(state)
            row.last_seen_at = occurred_at
            summary = self._instance_summary.setdefault(namespace, {}).setdefault(
                instance_id,
                InstanceNamespaceSummary(namespace=namespace, instance_id=instance_id),
            )
            summary.last_event_at = occurred_at
            summary.last_event_type = "block_read"
            summary.chunk_count = self._count_instance_chunks_locked(namespace, instance_id)
            return True

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
        self._remove_physical_location_locked(event)

    def _apply_all_blocks_cleared(self, event: CacheDomainEvent) -> None:
        namespace_map = self._chunk_locations.get(event.namespace, {})
        for chunk_key in list(namespace_map.keys()):
            namespace_map[chunk_key].pop(event.instance_id, None)
            if not namespace_map[chunk_key]:
                namespace_map.pop(chunk_key, None)
        self._remove_instance_physical_records_locked(event.namespace, event.instance_id)
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
        self._remove_instance_physical_records_locked(namespace, instance_id)
        self._instance_summary.get(namespace, {}).pop(instance_id, None)

    def _upsert_physical_location_locked(self, event: CacheDomainEvent) -> None:
        if not event.chunk_key or not event.physical_key:
            return
        physical_map = self._physical_chunk_locations.setdefault(event.namespace, {}).setdefault(event.chunk_key, {})
        existing = physical_map.get(event.physical_key)
        if existing is None:
            physical_map[event.physical_key] = PhysicalChunkLocationRecord(
                physical_key=event.physical_key,
                instance_id=event.instance_id,
                device=event.device,
                state=event.state,
                kv_rank=int(event.kv_rank or 0),
                object_group_id=int(event.object_group_id or 0),
                cache_salt=event.cache_salt,
                event_keys_count=event.event_keys_count,
                first_seen_at=event.occurred_at,
                last_seen_at=event.occurred_at,
            )
            return
        existing.device = event.device
        existing.state = event.state
        existing.event_keys_count = event.event_keys_count
        existing.last_seen_at = event.occurred_at

    def _remove_physical_location_locked(self, event: CacheDomainEvent) -> None:
        if not event.chunk_key or not event.physical_key:
            return
        physical_map = self._physical_chunk_locations.get(event.namespace, {}).get(event.chunk_key, {})
        physical_map.pop(event.physical_key, None)
        if not physical_map:
            self._physical_chunk_locations.get(event.namespace, {}).pop(event.chunk_key, None)

    def _remove_instance_physical_records_locked(self, namespace: str, instance_id: str) -> None:
        namespace_map = self._physical_chunk_locations.get(namespace, {})
        for chunk_key in list(namespace_map.keys()):
            namespace_map[chunk_key] = {
                physical_key: row
                for physical_key, row in namespace_map[chunk_key].items()
                if row.instance_id != instance_id
            }
            if not namespace_map[chunk_key]:
                namespace_map.pop(chunk_key, None)

    def _list_physical_locations_locked(
        self,
        namespace: str,
        chunk_key: str,
        instance_id: Optional[str],
    ) -> List[Dict[str, Any]]:
        rows = list(self._physical_chunk_locations.get(namespace, {}).get(chunk_key, {}).values())
        if instance_id is not None:
            rows = [row for row in rows if row.instance_id == instance_id]
        return [
            {
                "physical_key": row.physical_key,
                "instance_id": row.instance_id,
                "device": row.device,
                "state": row.state,
                "kv_rank": row.kv_rank,
                "object_group_id": row.object_group_id,
                "cache_salt": row.cache_salt,
                "event_keys_count": row.event_keys_count,
                "first_seen_at": row.first_seen_at,
                "last_seen_at": row.last_seen_at,
            }
            for row in sorted(rows, key=lambda item: (-int(item.last_seen_at), item.physical_key))
        ]

    def _count_instance_chunks_locked(self, namespace: str, instance_id: str) -> int:
        namespace_map = self._chunk_locations.get(namespace, {})
        total = 0
        for instance_map in namespace_map.values():
            if instance_id in instance_map:
                total += 1
        return total
