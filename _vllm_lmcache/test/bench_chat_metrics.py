#!/usr/bin/env python3
# bench_chat_metrics.py
import argparse
import asyncio
import aiohttp
import json
import time
import re
import pathlib

METRIC_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{[^}]*\})?\s+([-+0-9.eE]+)$")

WATCH_METRICS = [
    "vllm:request_success",
    "vllm:request_failure",
    "vllm:prompt_tokens",
    "vllm:generation_tokens",
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:gpu_cache_usage_perc",
    "vllm:num_preemptions",
    "vllm:prefix_cache_hits",
    "vllm:prefix_cache_queries",
    "lmcache:num_requested_tokens",
    "lmcache:num_hit_tokens",
    "lmcache:num_retrieve_requests",
    "lmcache:num_store_requests",
    "lmcache:num_lookup_requests",
]

def parse_metrics(text):
    metrics = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = METRIC_RE.match(line)
        if not m:
            continue

        name = m.group(1)
        value = float(m.group(2))

        if name.endswith("_total"):
            name = name[:-6]

        if name in WATCH_METRICS:
            metrics[name] = metrics.get(name, 0.0) + value

    return metrics

async def fetch_metrics(session, metrics_url):
    async with session.get(metrics_url, timeout=10) as resp:
        text = await resp.text()
        return parse_metrics(text)

def percentile(values, p):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    idx = round((len(values) - 1) * p / 100)
    return values[idx]

def build_prompt(mode, request_id, prompt_repeats):
    stable_prefix = (
        "下面是一段用于测试 vLLM 和 LMCache 的稳定长前缀。"
        "多次请求中，这段内容应该保持完全一致，用于观察 prefix cache 和 LMCache 命中情况。\n"
    ) * prompt_repeats

    question = f"\n请基于上文总结三个关键点。请求编号: {request_id}"

    if mode == "hot_prefix":
        return stable_prefix + question

    return f"唯一冷启动前缀-{request_id}-{time.time_ns()}\n" + stable_prefix + question

async def send_chat_request(session, args, request_id):
    prompt = build_prompt(args.mode, request_id, args.prompt_repeats)

    payload = {
        "model": args.model,
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
    }

    start = time.perf_counter()

    try:
        async with session.post(args.url, json=payload, timeout=args.timeout) as resp:
            text = await resp.text()
            end = time.perf_counter()

            if resp.status >= 400:
                return {
                    "id": request_id,
                    "ok": False,
                    "status": resp.status,
                    "latency_ms": (end - start) * 1000,
                    "error": text[:500],
                }

            data = json.loads(text)
            usage = data.get("usage", {})
            content = data["choices"][0]["message"].get("content", "")

            return {
                "id": request_id,
                "ok": True,
                "status": resp.status,
                "latency_ms": (end - start) * 1000,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "text_head": content[:120],
            }

    except Exception as e:
        end = time.perf_counter()
        return {
            "id": request_id,
            "ok": False,
            "latency_ms": (end - start) * 1000,
            "error": repr(e),
        }

async def main(args):
    out_dir = pathlib.Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    connector = aiohttp.TCPConnector(limit=args.concurrency + 8)

    async with aiohttp.ClientSession(connector=connector) as session:
        if args.prewarm > 0 and args.mode == "hot_prefix":
            for i in range(args.prewarm):
                await send_chat_request(session, args, f"prewarm-{i}")

        metrics_before = await fetch_metrics(session, args.metrics_url)
        wall_start = time.perf_counter()

        sem = asyncio.Semaphore(args.concurrency)

        async def guarded(i):
            async with sem:
                return await send_chat_request(session, args, i)

        results = await asyncio.gather(
            *(guarded(i) for i in range(args.num_requests))
        )

        wall_end = time.perf_counter()
        metrics_after = await fetch_metrics(session, args.metrics_url)

    ok_results = [r for r in results if r.get("ok")]
    latencies = [r["latency_ms"] for r in ok_results]
    completion_tokens = [
        r.get("completion_tokens") for r in ok_results
        if r.get("completion_tokens") is not None
    ]

    wall_s = wall_end - wall_start
    metrics_delta = {
        k: metrics_after.get(k, 0.0) - metrics_before.get(k, 0.0)
        for k in set(metrics_before) | set(metrics_after)
    }

    lmcache_requested = metrics_delta.get("lmcache:num_requested_tokens", 0.0)
    lmcache_hit = metrics_delta.get("lmcache:num_hit_tokens", 0.0)

    prefix_queries = metrics_delta.get("vllm:prefix_cache_queries", 0.0)
    prefix_hits = metrics_delta.get("vllm:prefix_cache_hits", 0.0)

    summary = {
        "mode": args.mode,
        "url": args.url,
        "metrics_url": args.metrics_url,
        "model": args.model,
        "num_requests": args.num_requests,
        "concurrency": args.concurrency,
        "max_tokens": args.max_tokens,
        "success": len(ok_results),
        "failed": len(results) - len(ok_results),
        "wall_s": wall_s,
        "request_per_s": len(ok_results) / wall_s if wall_s > 0 else None,
        "completion_token_per_s_client": sum(completion_tokens) / wall_s if completion_tokens and wall_s > 0 else None,
        "latency_p50_ms": percentile(latencies, 50),
        "latency_p95_ms": percentile(latencies, 95),
        "latency_p99_ms": percentile(latencies, 99),
        "metrics_delta": metrics_delta,
        "lmcache_token_hit_ratio": lmcache_hit / lmcache_requested if lmcache_requested else None,
        "vllm_prefix_hit_ratio": prefix_hits / prefix_queries if prefix_queries else None,
        "metrics_before": metrics_before,
        "metrics_after": metrics_after,
    }

    (out_dir / "details.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in results),
        encoding="utf-8",
    )

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000/v1/chat/completions")
    parser.add_argument("--metrics-url", default="http://127.0.0.1:8000/metrics")
    parser.add_argument("--model", default="llama-3-70b-instruct")
    parser.add_argument("--mode", choices=["cold", "hot_prefix"], default="hot_prefix")
    parser.add_argument("--num-requests", type=int, default=32)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--max-tokens", type=int, default=16)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--prompt-repeats", type=int, default=100)
    parser.add_argument("--prewarm", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--out", default="results/chat_metrics_run")
    asyncio.run(main(parser.parse_args()))