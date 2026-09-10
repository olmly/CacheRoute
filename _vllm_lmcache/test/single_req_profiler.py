import time
import requests
import json
from typing import Dict, Optional

def profile_single_request(prompt: str, api_url: str = "http://127.0.0.1:8000/v1/completions"):
    """发送单个请求并采集性能指标"""
    
    payload = {
        "model": "llama-3-70b-instruct",
        "prompt": prompt,
        "max_tokens": 512,
        "temperature": 0,
        "stream": False
    }
    
    start_time = time.perf_counter()
    
    try:
        response = requests.post(api_url, json=payload, timeout=120)
        end_time = time.perf_counter()
        
        # 调试：打印完整响应
        print(f"\n=== Response for prompt: '{prompt[:50]}...' ===")
        print(f"Status Code: {response.status_code}")
        
        if response.status_code != 200:
            print(f"Error Response: {response.text}")
            return None
        
        resp_json = response.json()
        print(f"Response keys: {resp_json.keys()}")
        print(f"Full response: {json.dumps(resp_json, indent=2)[:500]}")
        
        # 安全获取 token 数量
        output_tokens = 0
        if "usage" in resp_json:
            output_tokens = resp_json["usage"].get("completion_tokens", 0)
        elif "choices" in resp_json and len(resp_json["choices"]) > 0:
            # 如果没 usage，尝试从 choices 获取
            text = resp_json["choices"][0].get("text", "")
            output_tokens = len(text.split())
        
        # 计算指标
        total_latency = end_time - start_time
        
        # TTFT：从响应头获取（vLLM 可能返回）
        ttft = None
        if "x-ttft" in response.headers:
            ttft = float(response.headers["x-ttft"])
        
        # TPOT：如果支持 streaming，可以通过 token 数计算
        tpot = None
        if output_tokens > 0:
            tpot = (total_latency / output_tokens) * 1000  # ms/token
        
        metrics = {
            "prompt": prompt[:100] + "..." if len(prompt) > 100 else prompt,
            "prompt_len": len(prompt.split()),
            "output_tokens": output_tokens,
            "ttft": ttft,  # 秒
            "tpot": tpot,  # ms/token
            "total_latency": total_latency,  # 秒
            "status_code": response.status_code,
            "raw_response": resp_json
        }
        
        return metrics
        
    except requests.exceptions.Timeout:
        print(f"Timeout for prompt: {prompt[:50]}...")
        return None
    except Exception as e:
        print(f"Error: {e}")
        return None

def main():
    """测试不同长度的 prompts"""
    
    prompts = [
        "Hello",  # 短
        "Explain quantum computing in simple terms",  # 中
        "Write a detailed analysis of the economic impact of AI" * 20,  # 长
    ]
    
    print("Starting profiling...")
    print("=" * 60)
    
    results = []
    for i, prompt in enumerate(prompts):
        print(f"\n[{i+1}/{len(prompts)}] Testing prompt length: {len(prompt.split())} words")
        result = profile_single_request(prompt)
        
        if result:
            results.append(result)
            print(f"  ✅ Latency: {result['total_latency']:.2f}s")
            print(f"  📊 Output tokens: {result['output_tokens']}")
            if result['ttft']:
                print(f"  ⏱️  TTFT: {result['ttft']*1000:.2f}ms")
            if result['tpot']:
                print(f"  ⚡ TPOT: {result['tpot']:.2f}ms/token")
        else:
            print(f"  ❌ Request failed")
    
    # 汇总
    if results:
        print("\n" + "=" * 60)
        print("SUMMARY:")
        avg_latency = sum(r['total_latency'] for r in results) / len(results)
        avg_tokens = sum(r['output_tokens'] for r in results) / len(results)
        print(f"  Average latency: {avg_latency:.2f}s")
        print(f"  Average output tokens: {avg_tokens:.0f}")
        print(f"  Total requests: {len(results)}")

if __name__ == "__main__":
    main()