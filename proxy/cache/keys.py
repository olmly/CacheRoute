"""Stable proxy-side cache key helpers."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable, List


def _normalize_knowledge_ids(knowledge_ids: Iterable[Any]) -> List[str]:
    return [str(item).strip() for item in (knowledge_ids or []) if str(item).strip()]

#前缀键生成器？
def build_prefix_key(model: str, knowledge_ids: Iterable[Any], injection_type: str = "kvcache") -> str:
    """Build a stable proxy-facing prefix key without depending on LMCache internals."""
    payload = {
        "model": str(model or "").strip(),
        "injection_type": str(injection_type or "kvcache").strip().lower(),
        "knowledge_ids": _normalize_knowledge_ids(knowledge_ids),
    }
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()
