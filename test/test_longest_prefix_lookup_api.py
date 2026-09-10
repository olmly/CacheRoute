from fastapi.testclient import TestClient

from proxy.cache.event_adapter import CacheDomainEvent
from proxy.cache.index import CacheVisibilityIndex
from proxy.cache.query_service import CacheQueryService
from proxy.resource.instance_pool import InstancePool
from proxy.resource.p_control_plane import (
    _control_plane,
    set_cache_query_provider,
    set_pool,
)


def test_longest_prefix_lookup_returns_only_live_registered_instances():
    index = CacheVisibilityIndex()
    service = CacheQueryService(index)
    pool = InstancePool(ttl_s=30)
    pool.upsert(instance_id="127.0.0.1:8000", host="127.0.0.1", port=8000)
    set_pool(pool)
    set_cache_query_provider(service)

    namespace = "model::tokenizer::default"
    for chunk_key in ("chunk-a", "chunk-b"):
        index.apply_event(
            CacheDomainEvent(
                event_type="block_stored",
                namespace=namespace,
                instance_id="127.0.0.1:8000",
                occurred_at=1,
                chunk_key=chunk_key,
                device="cpu",
            )
        )
    index.apply_event(
        CacheDomainEvent(
            event_type="block_stored",
            namespace=namespace,
            instance_id="stale-instance",
            occurred_at=1,
            chunk_key="chunk-a",
            device="cpu",
        )
    )

    with TestClient(_control_plane) as api:
        response = api.post(
            "/v1/cache/longest-prefix-lookup",
            json={"namespace": namespace, "chunk_keys": ["chunk-a", "chunk-b"]},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["match_strategy"] == "longest_consecutive_prefix"
    assert payload["cache_match_count"] == 2
    assert payload["routable_match_count"] == 1
    assert payload["unroutable_instance_ids"] == ["stale-instance"]
    assert payload["matches"] == [
        {
            "instance_id": "127.0.0.1:8000",
            "matched_chunks": 2,
            "total_chunks": 2,
            "residency_score": 1.0,
            "devices": ["cpu"],
            "last_seen_at": 1,
            "host": "127.0.0.1",
            "port": 8000,
            "endpoints": [],
        }
    ]
