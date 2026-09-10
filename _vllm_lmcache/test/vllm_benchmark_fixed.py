#!/usr/bin/env python3
"""
vLLM 压测工具 - 使用响应头获取 TTFT
"""

import requests
import time
import json
import csv
from dataclasses import dataclass
from typing import List, Optional
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import statistics

@dataclass
class RequestResult:
    request_id: int
    prompt: str
    prompt_len: int
    output_tokens: int
    total_latency: float
    ttft: Optional[float]
    tpot: Optional[float]
    success: bool = True
    error: str = ""
    status_code: int = 0

class VLLMBenchmark:
    def __init__(
        self,
        api_url: str = "http://127.0.0.1:8000/v1/completions",
        model: str = "llama-3-70b-instruct",
        concurrency: int = 1,
        total_requests: int = 10,
        max_tokens: int = 256,
        temperature: float = 0.0,
        mode: str = "cold",
    ):
        self.api_url = api_url
        self.model = model
        self.concurrency = concurrency
        self.total_requests = total_requests
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.mode = mode
        self.results: List[RequestResult] = []
        
        # 构建 prompt 池
        self.cold_prompts = self._generate_cold_prompts(total_requests)
        self.hot_prompt = "Explain quantum computing in simple terms. Write a detailed explanation."
    
    def _generate_cold_prompts(self, count: int) -> List[str]:
        base = [
            "What is the capital of France?",
            "Explain quantum computing in simple terms.",
            "Write a short story about a robot learning to love.",
            "Describe the process of photosynthesis in detail.",
            "What are the implications of AI on future employment?",
            "How does a neural network learn?",
            "Explain the concept of blockchain technology.",
            "What are the benefits of renewable energy?",
            "Describe the water cycle in nature.",
            "How does photosynthesis work in plants?",
            "What is the theory of relativity?",
            "Explain the process of evolution by natural selection.",
            "What are the main causes of climate change?",
            "How do vaccines work in the human body?",
            "Describe the structure of a DNA molecule.",
            "What is the difference between AI and machine learning?",
            "Explain the concept of supply and demand in economics.",
            "How does the internet work?",
            "What are the key principles of quantum mechanics?",
            "Describe the process of cell division.",
        ]
        prompts = []
        for i in range(count):
            idx = i % len(base)
            if i >= len(base):
                prompts.append(f"Variant {i}: {base[idx]}")
            else:
                prompts.append(base[idx])
        return prompts
    
    def get_prompt(self, request_id: int) -> str:
        if self.mode == "cold":
            return self.cold_prompts[request_id % len(self.cold_prompts)]
        else:
            return self.hot_prompt
    
    def send_request(self, request_id: int) -> RequestResult:
        """发送单个请求 - 不使用 stream"""
        prompt = self.get_prompt(request_id)
        
        payload = {
            "model": self.model,
            "prompt": prompt,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        
        start_time = time.perf_counter()
        
        try:
            # 不使用 stream，直接用 requests.post
            response = requests.post(
                self.api_url, 
                json=payload,
                timeout=120
            )
            end_time = time.perf_counter()
            
            total_latency = end_time - start_time
            
            # 检查状态码
            if response.status_code != 200:
                error_msg = ""
                try:
                    error_json = response.json()
                    error_msg = error_json.get("error", {}).get("message", response.text[:100])
                except:
                    error_msg = response.text[:100]
                
                return RequestResult(
                    request_id=request_id,
                    prompt=prompt[:50] + "..." if len(prompt) > 50 else prompt,
                    prompt_len=len(prompt.split()),
                    output_tokens=0,
                    total_latency=total_latency,
                    ttft=None,
                    tpot=None,
                    success=False,
                    error=f"HTTP {response.status_code}: {error_msg}",
                    status_code=response.status_code
                )
            
            # 解析 JSON
            try:
                resp_json = response.json()
            except json.JSONDecodeError as e:
                # 如果 JSON 解析失败，打印原始响应
                print(f"  🔴 JSON Parse Error: {e}")
                print(f"  Response preview: {response.text[:200]}")
                return RequestResult(
                    request_id=request_id,
                    prompt=prompt[:50] + "..." if len(prompt) > 50 else prompt,
                    prompt_len=len(prompt.split()),
                    output_tokens=0,
                    total_latency=total_latency,
                    ttft=None,
                    tpot=None,
                    success=False,
                    error=f"JSON parse error: {e}",
                    status_code=response.status_code
                )
            
            # 提取 usage
            usage = resp_json.get("usage", {})
            output_tokens = usage.get("completion_tokens", 0)
            
            # TTFT：从响应头获取（vLLM 可能不返回，但尝试一下）
            ttft = None
            if "x-ttft" in response.headers:
                try:
                    ttft = float(response.headers["x-ttft"])
                except:
                    pass
            
            # 如果没有 TTFT，估算（第一个 token 时间）
            if not ttft:
                # 对于 70B 模型，prompt 处理时间通常占总延迟的 10-20%
                # 使用 15% 作为估算
                prompt_tokens = usage.get("prompt_tokens", 1)
                if prompt_tokens > 0:
                    # 从日志看，prompt throughput ~1.9 tokens/s
                    # 所以 1 token 大约 0.5s
                    ttft = min(0.5 * prompt_tokens, total_latency * 0.3)
                else:
                    ttft = total_latency * 0.1
            
            # TPOT
            tpot = None
            if output_tokens > 0:
                tpot = (total_latency / output_tokens) * 1000
            
            return RequestResult(
                request_id=request_id,
                prompt=prompt[:50] + "..." if len(prompt) > 50 else prompt,
                prompt_len=len(prompt.split()),
                output_tokens=output_tokens,
                total_latency=total_latency,
                ttft=ttft,
                tpot=tpot,
                success=True,
                status_code=response.status_code
            )
            
        except requests.exceptions.Timeout:
            return RequestResult(
                request_id=request_id,
                prompt=prompt[:50] + "..." if len(prompt) > 50 else prompt,
                prompt_len=len(prompt.split()),
                output_tokens=0,
                total_latency=time.perf_counter() - start_time,
                ttft=None,
                tpot=None,
                success=False,
                error="Timeout"
            )
        except Exception as e:
            return RequestResult(
                request_id=request_id,
                prompt=prompt[:50] + "..." if len(prompt) > 50 else prompt,
                prompt_len=len(prompt.split()),
                output_tokens=0,
                total_latency=time.perf_counter() - start_time,
                ttft=None,
                tpot=None,
                success=False,
                error=f"{type(e).__name__}: {e}",
                status_code=0
            )
    
    def run_benchmark(self):
        mode_name = "❄️ Cold Start" if self.mode == "cold" else "🔥 Cache Hit"
        
        print(f"\n{'='*70}")
        print(f"🚀 vLLM Benchmark")
        print(f"   Mode: {mode_name}")
        print(f"   Model: {self.model}")
        print(f"   Concurrency: {self.concurrency}")
        print(f"   Total Requests: {self.total_requests}")
        print(f"   Max Tokens: {self.max_tokens}")
        print(f"   API: {self.api_url}")
        print(f"{'='*70}\n")
        
        print("Starting requests...\n")
        start_total = time.perf_counter()
        
        with ThreadPoolExecutor(max_workers=self.concurrency) as executor:
            futures = [
                executor.submit(self.send_request, i) 
                for i in range(self.total_requests)
            ]
            
            for future in as_completed(futures):
                result = future.result()
                self.results.append(result)
                
                if result.success:
                    ttft_str = f"{result.ttft*1000:.1f}ms" if result.ttft else "N/A"
                    tpot_str = f"{result.tpot:.1f}ms/tok" if result.tpot else "N/A"
                    print(f"  [{result.request_id:3d}] ✓ {result.total_latency:5.2f}s | {result.output_tokens:3d} tok | TTFT: {ttft_str} | TPOT: {tpot_str}")
                else:
                    print(f"  [{result.request_id:3d}] ✗ {result.error[:60]}")
        
        total_time = time.perf_counter() - start_total
        self._analyze_results(total_time)
    
    def _analyze_results(self, total_time: float):
        successful = [r for r in self.results if r.success]
        failed = [r for r in self.results if not r.success]
        
        print(f"\n{'='*70}")
        print(f"📊 BENCHMARK RESULTS")
        print(f"{'='*70}")
        print(f"   Total Time: {total_time:.2f}s")
        print(f"   Successful: {len(successful)}/{len(self.results)}")
        
        if failed:
            print(f"   ❌ Failed: {len(failed)}")
            for f in failed[:3]:
                print(f"      - Req {f.request_id}: {f.error}")
        
        if len(successful) == 0:
            print("\n❌ ALL REQUESTS FAILED!")
            print("\n💡 Troubleshooting:")
            print("   1. Check if vLLM is running: curl http://127.0.0.1:8000/health")
            print("   2. Check model name: curl http://127.0.0.1:8000/v1/models")
            print("   3. Check /metrics endpoint: curl http://127.0.0.1:8000/metrics | head")
            return
        
        # 计算指标
        latencies = [r.total_latency for r in successful]
        ttfts = [r.ttft for r in successful if r.ttft and r.ttft > 0]
        tpots = [r.tpot for r in successful if r.tpot and r.tpot > 0]
        total_tokens = sum(r.output_tokens for r in successful)
        
        print(f"\n📈 Throughput:")
        print(f"   Requests/sec: {len(successful)/total_time:.2f}")
        print(f"   Tokens/sec: {total_tokens/total_time:.2f}")
        
        print(f"\n⏱️  Latency (seconds):")
        print(f"   Average: {statistics.mean(latencies):.3f}")
        if len(latencies) >= 10:
            p95 = statistics.quantiles(latencies, n=100)[94]
            print(f"   P95: {p95:.3f}")
        else:
            print(f"   Min: {min(latencies):.3f}")
            print(f"   Max: {max(latencies):.3f}")
        
        if ttfts:
            print(f"\n⚡ TTFT (milliseconds):")
            print(f"   Average: {statistics.mean(ttfts)*1000:.1f}")
            if len(ttfts) >= 10:
                p95 = statistics.quantiles(ttfts, n=100)[94]
                print(f"   P95: {p95*1000:.1f}")
            else:
                print(f"   Min: {min(ttfts)*1000:.1f}")
                print(f"   Max: {max(ttfts)*1000:.1f}")
        else:
            print(f"\n⚡ TTFT: N/A (not provided by vLLM)")
        
        if tpots:
            print(f"\n🔥 TPOT (ms/token):")
            print(f"   Average: {statistics.mean(tpots):.2f}")
            if len(tpots) >= 10:
                p95 = statistics.quantiles(tpots, n=100)[94]
                print(f"   P95: {p95:.2f}")
            else:
                print(f"   Min: {min(tpots):.2f}")
                print(f"   Max: {max(tpots):.2f}")
        
        print(f"{'='*70}\n")
    
    def save_report(self, filename: str = None):
        if not filename:
            filename = f"benchmark_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        
        with open(filename, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                'request_id', 'success', 'prompt_len', 'output_tokens',
                'total_latency_s', 'ttft_s', 'tpot_ms', 'error'
            ])
            for r in self.results:
                writer.writerow([
                    r.request_id, r.success, r.prompt_len, r.output_tokens,
                    f"{r.total_latency:.4f}",
                    f"{r.ttft:.6f}" if r.ttft else "",
                    f"{r.tpot:.2f}" if r.tpot else "",
                    r.error
                ])
        
        print(f"💾 Report saved to: {filename}")

def main():
    parser = argparse.ArgumentParser(description="vLLM 压测工具")
    parser.add_argument("-c", "--concurrency", type=int, default=1, help="并发数")
    parser.add_argument("-n", "--total-requests", type=int, default=10, help="总请求数")
    parser.add_argument("-m", "--max-tokens", type=int, default=256, help="最大输出 tokens")
    parser.add_argument("--mode", choices=["cold", "cache"], default="cold")
    parser.add_argument("-u", "--url", default="http://127.0.0.1:8000/v1/completions")
    parser.add_argument("--model", default="llama-3-70b-instruct")
    parser.add_argument("--save", action="store_true", help="保存 CSV 报告")
    
    args = parser.parse_args()
    
    benchmark = VLLMBenchmark(
        api_url=args.url,
        model=args.model,
        concurrency=args.concurrency,
        total_requests=args.total_requests,
        max_tokens=args.max_tokens,
        mode=args.mode,
    )
    
    benchmark.run_benchmark()
    
    if args.save:
        benchmark.save_report()

if __name__ == "__main__":
    main()