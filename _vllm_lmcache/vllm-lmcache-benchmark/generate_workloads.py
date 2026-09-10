#!/usr/bin/env python3
"""Generate deterministic, realistic prompt-mix JSONL workloads."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


REFERENCE_UNIT = """Document section {section}: The service processes requests through admission, prefill,
decode, and streaming delivery. Capacity is constrained by KV cache blocks, memory bandwidth, and
scheduler token budgets. A cache hit is valid only when the tokenized prefix and model configuration
match. Record evidence before changing a parameter, and compare tail latency with delivered throughput."""


def shared_context(repetitions: int) -> str:
    return "\n".join(REFERENCE_UNIT.format(section=index + 1) for index in range(repetitions))


def short_prompt(index: int) -> str:
    questions = [
        "Explain the difference between prefill and decode in two concise paragraphs.",
        "List three practical metrics for an online LLM inference service and explain each one.",
        "Explain why a high GPU utilization number alone does not prove that serving is healthy.",
        "Describe one trade-off when increasing maximum batch size for online generation.",
    ]
    return questions[index % len(questions)]


def rag_prompt(context: str, index: int) -> str:
    return (
        "Use only the following reference document.\n\n"
        f"<reference>\n{context}\n</reference>\n\n"
        f"Question {index}: Summarize the implications for scheduler design in five bullet points."
    )


def agent_prompt(context: str, index: int) -> str:
    return (
        "You are operating an LLM serving platform. Read the runbook below and produce a detailed "
        "incident-response plan with hypotheses, validation steps, and rollback conditions.\n\n"
        f"<runbook>\n{context}\n</runbook>\n\n"
        f"Incident {index}: TTFT P99 rises while generation throughput is flat."
    )


def make_mixed(requests: int, repeats: int) -> list[dict]:
    short_count = round(requests * 0.70)
    rag_count = round(requests * 0.20)
    agent_count = requests - short_count - rag_count
    context = shared_context(repeats)
    items = [
        {"name": "short_chat", "prompt": short_prompt(index), "max_tokens": 128}
        for index in range(short_count)
    ]
    items.extend(
        {"name": "shared_rag", "prompt": rag_prompt(context, index), "max_tokens": 256}
        for index in range(rag_count)
    )
    items.extend(
        {"name": "long_agent", "prompt": agent_prompt(context, index), "max_tokens": 512}
        for index in range(agent_count)
    )
    return sorted(items, key=lambda item: (len(item["prompt"]) % 97, item["name"]))


def make_shared_prefix(requests: int, repeats: int) -> list[dict]:
    context = shared_context(repeats)
    return [
        {"name": "shared_prefix_rag", "prompt": rag_prompt(context, index), "max_tokens": 256}
        for index in range(requests)
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=("mixed", "shared-prefix"), required=True)
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--shared-prefix-repeats", type=int, default=96)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.requests < 1 or args.shared_prefix_repeats < 1:
        parser.error("--requests and --shared-prefix-repeats must be positive")
    items = (
        make_mixed(args.requests, args.shared_prefix_repeats)
        if args.scenario == "mixed"
        else make_shared_prefix(args.requests, args.shared_prefix_repeats)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        for item in items:
            handle.write(json.dumps(item, ensure_ascii=True) + "\n")
    print(f"Generated {len(items)} requests in {args.output}")
    print("Prompt token counts are model-dependent; use benchmark output for actual counts.")


if __name__ == "__main__":
    main()
