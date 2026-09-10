#!/usr/bin/env python3
"""Print the small, experiment-relevant delta between two Prometheus snapshots."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


SAMPLE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)$")
LABEL = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="([^"]*)"')


def read_metrics(path: Path) -> tuple[dict[str, float], dict[str, list[dict[str, str]]]]:
    values: dict[str, float] = {}
    labels_by_name: dict[str, list[dict[str, str]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = SAMPLE.match(line)
        if not match:
            continue
        name, raw_labels, raw_value = match.groups()
        labels = dict(LABEL.findall(raw_labels or ""))
        # The model/engine labels are stable within one benchmark. Sum all matching samples.
        values[name] = values.get(name, 0.0) + float(raw_value)
        labels_by_name.setdefault(name, []).append(labels)
    return values, labels_by_name


def delta(before: dict[str, float], after: dict[str, float], name: str) -> float:
    return after.get(name, 0.0) - before.get(name, 0.0)


def average_ms(before: dict[str, float], after: dict[str, float], base: str) -> float | None:
    count = delta(before, after, f"{base}_count")
    if count <= 0:
        return None
    return delta(before, after, f"{base}_sum") * 1000 / count


def fmt(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--client-summary", type=Path, help="Optional summary-*.json from benchmark_chat.py")
    args = parser.parse_args()

    before, _ = read_metrics(args.before)
    after, labels = read_metrics(args.after)
    completed = delta(before, after, "http_requests_total")
    prompt = delta(before, after, "vllm:prompt_tokens_total")
    generated = delta(before, after, "vllm:generation_tokens_total")
    cached = delta(before, after, "vllm:prompt_tokens_cached_total")
    local_hits = delta(before, after, "vllm:prefix_cache_hits_total")
    external_hits = delta(before, after, "vllm:external_prefix_cache_hits_total")
    external_queries = delta(before, after, "vllm:external_prefix_cache_queries_total")

    print("=== 本轮 vLLM + LMCache 摘要 ===")
    print(f"请求: {fmt(completed, 0)} | Prompt token: {fmt(prompt, 0)} | 输出 token: {fmt(generated, 0)}")
    print(
        "延迟均值(ms): "
        f"TTFT={fmt(average_ms(before, after, 'vllm:time_to_first_token_seconds'))}, "
        f"queue={fmt(average_ms(before, after, 'vllm:request_queue_time_seconds'))}, "
        f"prefill={fmt(average_ms(before, after, 'vllm:request_prefill_time_seconds'))}, "
        f"decode={fmt(average_ms(before, after, 'vllm:request_decode_time_seconds'))}, "
        f"TPOT={fmt(average_ms(before, after, 'vllm:request_time_per_output_token_seconds'))}, "
        f"E2E={fmt(average_ms(before, after, 'vllm:e2e_request_latency_seconds'))}"
    )
    hit_rate = cached / prompt * 100 if prompt else None
    external_rate = external_hits / external_queries * 100 if external_queries else None
    print(
        "缓存: "
        f"cached_tokens={fmt(cached, 0)} ({fmt(hit_rate)}%), "
        f"local_hits={fmt(local_hits, 0)}, "
        f"external_hits={fmt(external_hits, 0)}/{fmt(external_queries, 0)} ({fmt(external_rate)}%)"
    )
    print(
        "快照末态: "
        f"running={fmt(after.get('vllm:num_requests_running'))}, "
        f"waiting={fmt(after.get('vllm:num_requests_waiting'))}, "
        f"KV_usage={fmt(after.get('vllm:kv_cache_usage_perc', 0) * 100)}%, "
        f"preemptions_total={fmt(after.get('vllm:num_preemptions_total'), 0)}"
    )

    config = labels.get("vllm:cache_config_info", [{}])[0]
    if config:
        print(
            "KV 配置: "
            f"prefix_caching={config.get('enable_prefix_caching', 'n/a')}, "
            f"block_size={config.get('block_size', 'n/a')}, "
            f"capacity={config.get('kv_cache_size_tokens', 'n/a')} tokens, "
            f"gpu_memory_utilization={config.get('gpu_memory_utilization', 'n/a')}"
        )

    lmcache_names = sorted(name for name in after if "lmcache" in name.lower())
    if lmcache_names:
        print("LMCache 原生指标: " + ", ".join(lmcache_names))
    else:
        print("LMCache 原生指标: 未在此快照中暴露；仅能通过 vLLM external_prefix_cache_* 观察外部缓存查询/命中。")

    if args.client_summary and args.client_summary.exists():
        client = json.loads(args.client_summary.read_text(encoding="utf-8"))
        print(
            "客户端汇总: "
            f"success={client.get('succeeded')}/{client.get('requests')}, "
            f"concurrency={client.get('concurrency')}, "
            f"prompt_tokens={client.get('total_prompt_tokens')}, "
            f"actual_output_tokens={client.get('total_completion_tokens')}, "
            f"RPS={client.get('request_throughput_rps')}, "
            f"output_tokens/s={client.get('generation_throughput_tokens_s')}"
        )
        if client.get("workload_plan"):
            print("场景计划: " + json.dumps(client["workload_plan"], ensure_ascii=False))
        else:
            print(
                "提示词计划: "
                f"prompt_chars={client.get('prompt_characters')}, "
                f"max_tokens={client.get('max_tokens')}"
            )


if __name__ == "__main__":
    main()
