"""Stable read-side query API for cache visibility index."""
from __future__ import annotations

from typing import Any, Dict, Optional

from .index import CacheVisibilityIndex


class CacheQueryService:
    """对外只暴露查询语义，不暴露索引内部结构。"""

    def __init__(self, index: CacheVisibilityIndex) -> None:
        self._index = index

    def get_instance_summary(self, instance_id: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        return {
            "instance_id": instance_id,
            "namespace": namespace,
            "summaries": self._index.get_instance_summary(instance_id=instance_id, namespace=namespace),
        }

    def get_chunk_locations(self, namespace: str, chunk_key: str) -> Dict[str, Any]:
        return {
            "namespace": namespace,
            "chunk_key": chunk_key,
            "locations": self._index.get_chunk_locations(namespace=namespace, chunk_key=chunk_key),
        }

    def get_namespace_summary(self, namespace: str) -> Dict[str, Any]:
        return self._index.get_namespace_summary(namespace)

    def get_index_stats(self) -> Dict[str, Any]:
        return self._index.get_index_stats()

    def lookup_prefix(self, namespace: str, prefix_key: str) -> Dict[str, Any]:
        return self._index.lookup_prefix(namespace=namespace, prefix_key=prefix_key)
