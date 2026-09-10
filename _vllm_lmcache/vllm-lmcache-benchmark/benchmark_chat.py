#!/usr/bin/env python3
"""Dependency-free streaming benchmark for an OpenAI-compatible vLLM endpoint."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * p / 100
    low, high = math.floor(index), math.ceil(index)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (index - low)


def finite_or_blank(value: object) -> object:
    return "" if value is None else value


def run_one(
    index: int, args: argparse.Namespace, start_gate: threading.Event, work_item: dict
) -> dict:
    start_gate.wait()
    started = time.perf_counter()
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": work_item["prompt"]}],
        "max_tokens": work_item["max_tokens"],
        "temperature": args.temperature,
        "stream": True,
        # vLLM supports this OpenAI extension. Older servers may omit usage harmlessly.
        "stream_options": {"include_usage": True},
    }
    request = urllib.request.Request(
        f"{args.base_url.rstrip('/')}/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    first_token_at = None
    last_token_at = None
    content_chunks = 0
    usage: dict = {}

    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                event = json.loads(data)
                if event.get("usage"):
                    usage = event["usage"]
                for choice in event.get("choices", []):
                    delta = choice.get("delta") or {}
                    # Ignore role-only chunks. Count only generated text/reasoning chunks.
                    if delta.get("content") or delta.get("reasoning_content"):
                        now = time.perf_counter()
                        first_token_at = first_token_at or now
                        last_token_at = now
                        content_chunks += 1
        completed = time.perf_counter()
        if first_token_at is None:
            raise RuntimeError("stream ended without a generated token")
        output_tokens = usage.get("completion_tokens")
        tpot = None
        if output_tokens and output_tokens > 1 and last_token_at is not None:
            tpot = (last_token_at - first_token_at) / (output_tokens - 1)
        return {
            "request": index,
            "workload": work_item["name"],
            "requested_max_tokens": work_item["max_tokens"],
            "ok": True,
            "started_at_s": started,
            "ttft_s": first_token_at - started,
            "e2e_s": completed - started,
            "tpot_s": tpot,
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": output_tokens,
            "content_chunks": content_chunks,
            "error": "",
        }
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError, json.JSONDecodeError) as error:
        completed = time.perf_counter()
        return {
            "request": index,
            "workload": work_item["name"],
            "requested_max_tokens": work_item["max_tokens"],
            "ok": False,
            "started_at_s": started,
            "ttft_s": None,
            "e2e_s": completed - started,
            "tpot_s": None,
            "prompt_tokens": None,
            "completion_tokens": None,
            "content_chunks": content_chunks,
            "error": str(error),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", default="Explain continuous batching in two sentences.")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument(
        "--workload-file",
        type=Path,
        help="JSONL with prompt; optional name and max_tokens per line.",
    )
    source_group.add_argument(
        "--prompt-file",
        type=Path,
        help="UTF-8 text file: one prompt per non-empty, non-comment line.",
    )
    parser.add_argument("--max-tokens", type=int, default=64)
    parser.add_argument("--temperature", type=float, default=0)
    parser.add_argument(
        "--requests",
        type=int,
        help="Total requests. Defaults to all prompts when --prompt-file is used; otherwise 20.",
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--output-dir", default="results")
    args = parser.parse_args()

    if args.requests is not None and args.requests < 1:
        parser.error("--requests must be positive")
    if args.concurrency < 1:
        parser.error("--concurrency must be positive")

    if args.workload_file:
        source_items = []
        for line_number, line in enumerate(
            args.workload_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            item = json.loads(line)
            if not item.get("prompt"):
                parser.error(f"workload line {line_number} has no prompt")
            source_items.append(
                {
                    "name": item.get("name", "unnamed"),
                    "prompt": item["prompt"],
                    "max_tokens": int(item.get("max_tokens", args.max_tokens)),
                }
            )
        if not source_items:
            parser.error("--workload-file contains no requests")
        request_count = args.requests or 20
        work_items = [source_items[index % len(source_items)] for index in range(request_count)]
    elif args.prompt_file:
        source_items = []
        for line_number, line in enumerate(
            args.prompt_file.read_text(encoding="utf-8").splitlines(), start=1
        ):
            prompt = line.strip()
            if not prompt or prompt.startswith("#"):
                continue
            source_items.append(
                {
                    "name": f"prompt_line_{line_number}",
                    "prompt": prompt,
                    "max_tokens": args.max_tokens,
                }
            )
        if not source_items:
            parser.error("--prompt-file contains no prompts")
        request_count = args.requests or len(source_items)
        if request_count > len(source_items):
            parser.error(
                "--requests cannot exceed the number of prompts in --prompt-file; "
                "add prompts or omit --requests"
            )
        work_items = source_items[:request_count]
    else:
        request_count = args.requests or 20
        work_items = [
            {"name": "default", "prompt": args.prompt, "max_tokens": args.max_tokens}
            for _ in range(request_count)
        ]

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    start_gate = threading.Event()
    results: list[dict] = []
    wall_started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [
            executor.submit(run_one, i, args, start_gate, work_items[i])
            for i in range(request_count)
        ]
        start_gate.set()
        for future in as_completed(futures):
            results.append(future.result())
    wall_s = time.perf_counter() - wall_started
    results.sort(key=lambda item: item["request"])

    successful = [item for item in results if item["ok"]]
    ttfts = [item["ttft_s"] for item in successful if item["ttft_s"] is not None]
    tpots = [item["tpot_s"] for item in successful if item["tpot_s"] is not None]
    output_tokens = sum(item["completion_tokens"] or 0 for item in successful)
    prompt_tokens = sum(item["prompt_tokens"] or 0 for item in successful)
    workload_counts: dict[str, int] = {}
    workload_plan: dict[str, dict] = {}
    for item in work_items:
        workload_counts[item["name"]] = workload_counts.get(item["name"], 0) + 1
        plan = workload_plan.setdefault(
            item["name"],
            {
                "requests": 0,
                "prompt_characters_min": len(item["prompt"]),
                "prompt_characters_max": len(item["prompt"]),
                "requested_max_tokens": sorted({item["max_tokens"]}),
            },
        )
        plan["requests"] += 1
        plan["prompt_characters_min"] = min(plan["prompt_characters_min"], len(item["prompt"]))
        plan["prompt_characters_max"] = max(plan["prompt_characters_max"], len(item["prompt"]))
    summary = {
        "run_id": run_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": args.base_url,
        "model": args.model,
        "requests": request_count,
        "concurrency": args.concurrency,
        "max_tokens": args.max_tokens,
        "prompt_characters": len(args.prompt) if not (args.workload_file or args.prompt_file) else None,
        "workload_file": str(args.workload_file) if args.workload_file else None,
        "prompt_file": str(args.prompt_file) if args.prompt_file else None,
        "workload_counts": workload_counts,
        "workload_plan": workload_plan,
        "total_prompt_tokens": prompt_tokens or None,
        "total_completion_tokens": output_tokens or None,
        "wall_s": round(wall_s, 4),
        "succeeded": len(successful),
        "failed": request_count - len(successful),
        "request_throughput_rps": round(len(successful) / wall_s, 4),
        "generation_throughput_tokens_s": round(output_tokens / wall_s, 4) if output_tokens else None,
        "ttft_ms": {f"p{p}": round(percentile(ttfts, p) * 1000, 2) if ttfts else None for p in (50, 90, 95, 99)},
        "tpot_ms": {f"p{p}": round(percentile(tpots, p) * 1000, 2) if tpots else None for p in (50, 90, 95, 99)},
    }
    csv_path = output_dir / f"requests-{run_id}.csv"
    json_path = output_dir / f"summary-{run_id}.json"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(results[0].keys()))
        writer.writeheader()
        writer.writerows([{key: finite_or_blank(value) for key, value in item.items()} for item in results])
    json_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Per-request results: {csv_path}")
    print(f"Summary: {json_path}")


if __name__ == "__main__":
    main()
