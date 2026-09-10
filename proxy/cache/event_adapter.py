"""Normalize raw ZMQ cache visibility messages into stable internal events."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class CacheDomainEvent:
    """Proxy-internal normalized cache event."""

    event_type: str
    namespace: str
    instance_id: str
    occurred_at: int
    chunk_key: Optional[str] = None
    physical_key: Optional[str] = None
    kv_rank: Optional[int] = None
    object_group_id: Optional[int] = None
    cache_salt: Optional[str] = None
    event_keys_count: Optional[int] = None
    boot_id: Optional[str] = None
    device: str = "unknown"
    state: str = "present"
    prefix_key: Optional[str] = None
    chunk_keys: Tuple[str, ...] = ()
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AdapterResult:
    ok: bool
    event: Optional[CacheDomainEvent] = None
    error: Optional[str] = None
    raw_summary: Optional[str] = None


def adapt_raw_event(raw_message: Any) -> AdapterResult:
    """Adapt a raw ZMQ payload into a stable cache-domain event."""
    try:
        payload = _coerce_payload(raw_message)
    except Exception as exc:
        return AdapterResult(
            ok=False,
            error=f"decode_failed:{type(exc).__name__}:{exc}",
            raw_summary=_summarize_raw(raw_message),
        )

    if not isinstance(payload, dict):
        return AdapterResult(
            ok=False,
            error="payload_not_dict",
            raw_summary=_summarize_raw(payload),
        )

    event_name = _pick_first(payload, "event_type", "event", "type", "kind")
    event_type = _normalize_event_type(event_name)
    if event_type is None:
        return AdapterResult(
            ok=False,
            error=f"unsupported_event_type:{event_name}",
            raw_summary=_summarize_raw(payload),
        )

    namespace = _build_namespace(payload)
    instance_id = _pick_first(
        payload,
        "instance_id",
        "instanceId",
        "worker_id",
        "workerId",
        "node_id",
        "nodeId",
    )
    if not instance_id and event_type not in {"all_blocks_cleared"}:
        return AdapterResult(
            ok=False,
            error="missing_instance_id",
            raw_summary=_summarize_raw(payload),
        )

    occurred_at = _as_int(
        _pick_first(payload, "occurred_at", "timestamp_ms", "timestamp", "ts_ms", "ts")
    ) or int(time.time() * 1000)
    chunk_key = _pick_first(
        payload,
        "chunk_key",
        "chunkKey",
        "block_key",
        "blockKey",
        "cache_key",
        "cacheKey",
    )
    prefix_key = _pick_first(payload, "prefix_key", "prefixKey")
    chunk_keys = _normalize_chunk_keys(payload.get("chunk_keys") or payload.get("chunkKeys"))
    object_key = payload.get("object_key") if isinstance(payload.get("object_key"), dict) else {}
    kv_rank = _as_int(object_key.get("kv_rank", payload.get("kv_rank")))
    object_group_id = _as_int(object_key.get("object_group_id", payload.get("object_group_id")))
    cache_salt = object_key.get("cache_salt", payload.get("cache_salt"))
    event_keys_count = _as_int(payload.get("event_keys_count"))
    boot_id = _pick_first(payload, "boot_id", "bootId")
    device = str(_pick_first(payload, "device", "tier", "storage_tier") or "unknown").strip().lower() or "unknown"
    state = str(
        _pick_first(payload, "state", "status") or _default_state_for_event(event_type)
    ).strip().lower() or _default_state_for_event(event_type)

    if event_type in {"block_stored", "block_evicted", "block_read"} and not chunk_key:
        return AdapterResult(
            ok=False,
            error="missing_chunk_key",
            raw_summary=_summarize_raw(payload),
        )

    event = CacheDomainEvent(
        event_type=event_type,
        namespace=namespace,
        instance_id=str(instance_id or "global"),
        occurred_at=occurred_at,
        chunk_key=str(chunk_key) if chunk_key else None,
        physical_key=_build_physical_key(
            chunk_key=str(chunk_key) if chunk_key else None,
            model_name=object_key.get("model_name", _pick_first(payload, "model", "model_name")),
            kv_rank=kv_rank,
            object_group_id=object_group_id,
            cache_salt=str(cache_salt) if cache_salt is not None else None,
        ),
        kv_rank=kv_rank,
        object_group_id=object_group_id,
        cache_salt=str(cache_salt) if cache_salt is not None else None,
        event_keys_count=event_keys_count,
        boot_id=str(boot_id) if boot_id else None,
        device=device,
        state=state,
        prefix_key=str(prefix_key) if prefix_key else None,
        chunk_keys=chunk_keys,
        raw=dict(payload),
    )
    return AdapterResult(ok=True, event=event, raw_summary=_summarize_raw(payload))


def _coerce_payload(raw_message: Any) -> Any:
    if isinstance(raw_message, (bytes, bytearray)):
        text = raw_message.decode("utf-8", errors="replace").strip()
        return json.loads(text) if text else {}
    if isinstance(raw_message, str):
        text = raw_message.strip()
        return json.loads(text) if text else {}
    return raw_message


def _normalize_event_type(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    aliases = {
        "blockstored": "block_stored",
        "block_stored": "block_stored",
        "stored": "block_stored",
        "store": "block_stored",
        "admit": "block_stored",
        "blockread": "block_read",
        "block_read": "block_read",
        "read": "block_read",
        "blockevicted": "block_evicted",
        "block_evicted": "block_evicted",
        "evicted": "block_evicted",
        "evict": "block_evicted",
        "allblockscleared": "all_blocks_cleared",
        "all_blocks_cleared": "all_blocks_cleared",
        "cleared": "all_blocks_cleared",
        "clear": "all_blocks_cleared",
        "instanceregistered": "instance_registered",
        "instance_registered": "instance_registered",
        "register": "instance_registered",
        "instanceunregistered": "instance_unregistered",
        "instance_unregistered": "instance_unregistered",
        "unregister": "instance_unregistered",
    }
    key = text.replace("-", "_").replace(" ", "_")
    return aliases.get(key) or aliases.get(key.replace("_", ""))


def _build_namespace(payload: Dict[str, Any]) -> str:
    explicit = _pick_first(payload, "namespace", "ns")
    if isinstance(explicit, dict):
        model = str(explicit.get("model") or "unknown_model").strip()
        tokenizer = str(explicit.get("tokenizer") or "unknown_tokenizer").strip()
        template = str(explicit.get("template_version") or "default").strip()
        return f"{model}::{tokenizer}::{template}"
    if explicit:
        return str(explicit).strip()

    model = str(_pick_first(payload, "model", "model_name") or "unknown_model").strip()
    tokenizer = str(
        _pick_first(payload, "tokenizer", "tokenizer_family", "tokenizer_version")
        or "unknown_tokenizer"
    ).strip()
    template = str(
        _pick_first(payload, "template_version", "prompt_template_version", "template")
        or "default"
    ).strip()
    return f"{model}::{tokenizer}::{template}"


def _normalize_chunk_keys(value: Any) -> Tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    ordered: List[str] = []
    seen = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return tuple(ordered)


def _build_physical_key(
    chunk_key: Optional[str],
    model_name: Any,
    kv_rank: Optional[int],
    object_group_id: Optional[int],
    cache_salt: Optional[str],
) -> Optional[str]:
    """Build a stable identity for one LMCache ObjectKey replica."""
    if not chunk_key or kv_rank is None or object_group_id is None:
        return None
    return "@".join(
        (
            str(model_name or "unknown_model"),
            str(kv_rank),
            str(object_group_id),
            chunk_key,
            cache_salt or "",
        )
    )


def _default_state_for_event(event_type: str) -> str:
    if event_type == "block_read":
        return "read"
    if event_type == "block_evicted":
        return "evicted"
    if event_type == "all_blocks_cleared":
        return "cleared"
    return "present"


def _pick_first(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload.get(key) is not None:
            return payload.get(key)
    return None


def _as_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip()
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _summarize_raw(payload: Any) -> str:
    text = str(payload)
    return text[:240] + ("..." if len(text) > 240 else "")
