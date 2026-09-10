# vLLM + LMCache 压测工具使用说明

本项目是压测客户端，向已启动的 vLLM/OpenAI 兼容服务发送流式 `POST /v1/chat/completions` 请求，保存逐请求延迟、成功率、吞吐和 token 统计。部分脚本还会采集 `/metrics`，比较测试前后的服务端指标。

**本项目不会启动 vLLM、加载模型或启用 LMCache。** 使用前需自行启动推理服务并配置缓存；不启用 LMCache 也可以进行基础压测。

## 1. 环境准备与快速开始

- Python 3.10+，仅使用标准库，不需要 `pip install`。
- 一键 `.sh` 脚本需要 Bash、`python3`、`curl` 及常见 Unix 工具（如 `awk`、`tee`、`date`）。建议在 Linux 或 WSL 中运行。
- 客户端只需能访问服务，不必与服务在同一台机器上，也不需要本地 GPU。
- 所有命令均从本项目目录运行，脚本内部使用相对路径。

下面是 Bash 命令。远程服务请将地址换成实际可访问的主机和端口：

```bash
cd vllm-lmcache-benchmark
chmod +x *.sh
export BASE_URL="http://127.0.0.1:8000"

# 查看可用模型，将返回的 data[].id 填入 MODEL
curl --fail --silent --show-error "$BASE_URL/v1/models"
export MODEL="你的实际模型ID"

# 检查指标接口；带 observed 的脚本和公共前缀预热脚本都需要它
curl --fail --silent --show-error "$BASE_URL/metrics"

# 编辑 prompts.txt 后，按文件顺序运行其中的全部提示词
CONCURRENCY=1 MAX_TOKENS=64 ./run_baseline_observed.sh
```

`BASE_URL` 只填写服务根地址，**不要加 `/v1`**，客户端会自动拼接 `/v1/chat/completions`。通过 `export` 设置地址和模型，子脚本才能获得这些变量。

若 `/metrics` 不可用，可先使用不采集指标的基线：

```bash
CONCURRENCY=1 ./run_baseline.sh
```

完成后检查终端 JSON 中的 `succeeded`、`failed`，以及 `results/baseline/` 下的文件。请求失败也可能正常结束进程，不能只用退出码判断压测成功。

### Windows PowerShell 直接运行

PowerShell 不能直接执行上述 Bash 语法；可使用 WSL，或直接运行 Python 客户端：

```powershell
python benchmark_chat.py --base-url http://127.0.0.1:8000 --model "你的实际模型ID" --requests 1 --concurrency 1 --max-tokens 64 --output-dir results/baseline
```

直接运行 Python 时，`BASE_URL`、`MODEL` 等环境变量不会自动作为参数读取，必须通过命令行选项传入。

## 2. 填写提示词文件

编辑项目根目录的 `prompts.txt`。每个非空行是一条提示词；空行和以 `#` 开头的说明行会跳过。例如：

```text
# 这是说明行，不会发送
解释 KV cache 如何影响推理并发能力。
用三句话说明 continuous batching 的价值。
```

`run_baseline.sh`、`run_baseline_observed.sh`、`run_sweep.sh` 和 `run_sweep_observed.sh` 都会读取此文件。未设置请求数时，每次从首行到末行各执行一次，CSV 的 `workload` 列用 `prompt_line_行号` 标识来源。设置 `CONCURRENCY=1` 时，提示词也按文件顺序逐条发送；更高并发仍按顺序分配提示词，但服务端实际开始和完成顺序可能不同。

可以改用另一个文件：`PROMPT_FILE=我的提示词.txt ./run_baseline.sh`。提示词不能跨多行；需要多行内容或每条不同的 `max_tokens` 时，请使用后文的 JSONL workload 文件。

## 3. 各脚本的用途与默认配置

| 文件 | 用途 | 默认请求数 / 并发 | 结果目录 |
|---|---|---|---|
| `run_baseline.sh` | 按 `prompts.txt` 顺序发送，不采集指标 | 文件全部提示词 / 1 | `results/baseline/` |
| `run_baseline_observed.sh` | 基线 + 前后指标快照 + 终端差分摘要 | 文件全部提示词 / 1 | `results/baseline/` |
| `run_sweep.sh` | 逐档按 `prompts.txt` 顺序发送，不采集指标 | 每档文件全部提示词 / 1、2、4、8、16、32 | `results/sweep-c<并发>/` |
| `run_sweep_observed.sh` | 每个并发档位分别采集、比较指标 | 同上 | 同上 |
| `run_mixed_observed.sh` | 短问答、长文档 RAG、Agent 提示词混合 | **2 / 1** | `results/mixed/` |
| `run_shared_prefix_warm.sh` | 一条长前缀请求预热，再正式测量 | 正式测量 32 / 8 | `results/shared-prefix-warm/` |
| `benchmark_chat.py` | 通用流式压测入口 | 20 / 4 | `results/` |
| `generate_workloads.py` | 生成 mixed 或 shared-prefix JSONL | 默认生成 100 条 | 由 `--output` 指定 |
| `capture_metrics.sh` | 抓取一次 `/metrics` | — | `results/metrics/` |
| `summarize_metrics.py` | 比较两份快照，可附客户端摘要 | — | 只打印终端摘要 |
| `observe_live.sh` | 周期采样服务端调度状态 | 默认间隔 0.5 秒 | `results/live/` |
| `record-template.md` | 手工记录环境、实验结果和结论 | — | 自行另存记录 |

## 4. 常用测试流程

### 4.1 顺序基线

```bash
CONCURRENCY=1 MAX_TOKENS=64 ./run_baseline_observed.sh
```

可重复运行，分别记录初次请求与后续稳定结果。脚本不会自动清空缓存，因此第一次执行脚本不一定代表冷缓存状态。

### 4.2 并发阶梯

```bash
CONCURRENCY_LEVELS="1 4 8 16 32" \
MAX_TOKENS=128 \
./run_sweep_observed.sh
```

每个并发档位都会按 `prompts.txt` 的顺序发送全部提示词。只测试 8 并发时，设置 `CONCURRENCY_LEVELS="8"`。无指标接口时换成 `./run_sweep.sh`。若只想运行前 N 条，可设置 `REQUESTS_PER_LEVEL=N`；N 不能超过文件中的有效提示词数。

`CONCURRENCY` 表示客户端最多同时执行的请求数，不是服务端真实 batch size。客户端没有固定 RPS 或到达率控制：有请求结束就继续发送下一条。请求总数少于并发数时，不可能达到设定并发。

### 4.3 混合场景

```bash
REQUESTS=100 CONCURRENCY=16 PREFIX_REPEATS=96 ./run_mixed_observed.sh
```

生成器按以下目标比例分配请求，使用确定性顺序发送，不是随机到达流量：

| 场景名（CSV 的 workload 列） | 目标占比 | 每请求 max_tokens |
|---|---:|---:|
| `short_chat`：短问答 | 70% | 128 |
| `shared_rag`：相同长文档前缀 + 末尾问题 | 20% | 256 |
| `long_agent`：运维手册 + 事件分析提示词 | 10% | 512 |

短问答和 RAG 数量分别四舍五入，剩余数量分配给 Agent。默认只有 2 个请求，不适合验证目标比例；上例 100 个请求对应 70/20/10。Agent 场景仍是一条生成请求，不执行工具调用或多轮工作流。

生成文件为 `results/workloads/mixed-100.jsonl`。此脚本使用文件内的输出上限，设置环境变量 `MAX_TOKENS` 不会改变它。

### 4.4 长公共前缀预热与测量

```bash
REQUESTS=32 CONCURRENCY=8 PREFIX_REPEATS=96 CACHE_SETTLE_SECONDS=3 \
./run_shared_prefix_warm.sh
```

执行顺序：

1. 生成 `results/workloads/shared-prefix-32.jsonl`，每条请求输出上限为 256 token。
2. 取文件第一条请求，以 1 并发预热，结果保存到 `results/shared-prefix-warmup/`。
3. 等待 `CACHE_SETTLE_SECONDS` 秒，默认 3 秒。
4. 采集正式测量前快照，执行全部请求，再采集快照并打印差分。

正式测量仍包含预热用的第一条请求，其余请求共享相同文档前缀、改变末尾问题。预热不计入正式测量的客户端统计；等待固定时间不代表已确认缓存写入完成，需结合服务端日志和命中指标判断。

`PREFIX_REPEATS` 是文档段落重复次数，默认 96，**不是 token 数**。若超出模型上下文长度，降低该值。环境变量 `MAX_TOKENS` 对本脚本同样无效。

## 5. 可设置的环境变量

以下变量由 Bash 包装脚本读取；Python 客户端参数见下一节。

| 变量 | 默认值 | 适用范围 |
|---|---|---|
| `BASE_URL` | `http://127.0.0.1:8000` | 请求与指标脚本 |
| `MODEL` | 必填 | 所有发请求的包装脚本 |
| `PROMPT_FILE` | `prompts.txt` | 基线、阶梯 |
| `MAX_TOKENS` | 64 | 基线、阶梯 |
| `REQUESTS` | 基线为文件全部提示词；混合 2；前缀测量 32 | 基线、混合、前缀测量 |
| `CONCURRENCY` | 基线 1；混合 1；前缀测量 8 | 基线、混合、前缀测量 |
| `REQUESTS_PER_LEVEL` | 每档文件全部提示词 | 阶梯；设置时不能超过提示词数 |
| `CONCURRENCY_LEVELS` | `1 2 4 8 16 32` | 阶梯 |
| `PREFIX_REPEATS` | 96 | 混合、前缀测量 |
| `CACHE_SETTLE_SECONDS` | 3 | 前缀测量 |
| `INTERVAL` | 0.5 秒 | 实时观察 |

`MAX_TOKENS` 只是生成上限，模型可能提前结束。环境变量会影响当前终端后续命令；使用上面的 `变量=值 ./脚本` 写法可为单次运行指定配置。

## 6. 直接调用客户端与自定义数据

```bash
python3 benchmark_chat.py \
  --base-url "$BASE_URL" --model "$MODEL" \
  --prompt "解释 KV cache 的作用。" \
  --requests 40 --concurrency 8 --max-tokens 128 \
  --temperature 0 --timeout 300 --output-dir results/custom
```

| 参数 | 默认值 | 含义 |
|---|---|---|
| `--base-url` | `http://127.0.0.1:8000` | 服务根地址 |
| `--model` | 必填 | 服务接受的模型 ID |
| `--prompt` | 英文 continuous batching 问题 | 单一用户提示词 |
| `--workload-file` | 未设置 | UTF-8 JSONL 请求文件；设置后使用文件提示词 |
| `--prompt-file` | 未设置 | UTF-8 文本文件，每个非空非注释行是一条提示词；与 `--workload-file` 互斥 |
| `--requests` | 普通/JSONL 为 20；文本提示词文件为全部行 | 总请求数，必须大于 0 |
| `--concurrency` | 4 | 客户端线程数，必须大于 0 |
| `--max-tokens` | 64 | 输出上限；文件每行可覆盖 |
| `--temperature` | 0 | 采样温度 |
| `--timeout` | 300 秒 | urllib 连接/读取超时，不是整轮测试时限 |
| `--output-dir` | `results` | 输出目录 |

自定义 `workload.jsonl`：每行一个 JSON 对象，`prompt` 必填且非空，`name` 和 `max_tokens` 可选。

```jsonl
{"name":"short","prompt":"解释连续批处理。","max_tokens":64}
{"name":"rag","prompt":"参考文档：这里填入固定长文档。问题：总结文档。","max_tokens":256}
```

```bash
python3 benchmark_chat.py --base-url "$BASE_URL" --model "$MODEL" \
  --workload-file workload.jsonl --requests 20 --concurrency 4 \
  --output-dir results/custom-workload
```

请求数大于文件行数时循环使用条目；小于行数时只使用前几条。文件行内 `max_tokens` 优先，缺省才用 `--max-tokens`。所有请求均构造为单条 `user` 消息，不支持从 JSONL 直接传入完整 `messages`。

直接运行文本提示词文件：

```bash
python3 benchmark_chat.py --base-url "$BASE_URL" --model "$MODEL" \
  --prompt-file prompts.txt --concurrency 1 --max-tokens 128 \
  --output-dir results/custom-prompts
```

不传 `--requests` 时会运行文件全部提示词；传入时只运行前 N 条，且 N 不能超过有效提示词数，避免在一次测试中重复循环提示词。

也可以先生成再编辑：

```bash
python3 generate_workloads.py --scenario mixed --requests 100 \
  --shared-prefix-repeats 96 --output results/workloads/custom.jsonl
```

编辑后直接通过 `benchmark_chat.py --workload-file` 运行；再次执行混合/前缀包装脚本会重新生成其对应文件，覆盖同名文件。

## 7. 结果如何查看

每轮输出 `summary-<UTC时间>.json` 和 `requests-<UTC时间>.csv`，不会保存模型生成的完整文本。

| 汇总字段 | 含义 |
|---|---|
| `succeeded` / `failed` | 成功 / 失败请求数 |
| `wall_s` | 整轮客户端耗时 |
| `request_throughput_rps` | 成功请求数 / 整轮耗时 |
| `generation_throughput_tokens_s` | 成功请求输出 token 总数 / 整轮耗时 |
| `ttft_ms` | 客户端首个非空文本或 reasoning 片段延迟，P50/P90/P95/P99，毫秒 |
| `tpot_ms` | 每请求平均输出 token 间隔的分位数，毫秒 |
| `total_prompt_tokens` / `total_completion_tokens` | 成功请求的输入 / 输出 token 总数 |
| `workload_counts` / `workload_plan` | 场景数量、提示词字符数范围和输出上限信息 |

CSV 包含 `workload`、`ok`、`requested_max_tokens`、`ttft_s`、`e2e_s`、`tpot_s`、`prompt_tokens`、`completion_tokens`、`content_chunks` 和 `error` 等字段。CSV 延迟单位为秒；`started_at_s` 是本机单调计时值，不是日期时间。

指标解读注意：

- 客户端 TTFT 包含网络和服务端排队等开销，但不包含任务在线程池等待空闲线程的时间。
- TPOT 按 `(最后文本片段时间 - 首个文本片段时间) / (输出 token 数 - 1)` 计算，是每请求平均值；不是所有相邻 token 间隔的分布。流式片段可能包含多个 token。
- 服务端不返回流式 `usage` 时，token 统计和 TPOT 可能为 `null`/空白；`content_chunks` 不能当作 token 数。只有一个输出 token 时也没有 TPOT。
- JSON 顶层 `max_tokens` 是客户端默认参数，使用 workload 时应以逐请求 CSV 和 JSONL 为准。同名场景存在多个输出上限时，当前 `workload_plan` 只记录首次遇到的上限。
- 客户端分位数只统计成功请求。单请求或少量样本不足以评估 P95/P99，应同时检查失败率和样本量。
- 文件名精度为秒，同一输出目录内同秒启动多轮可能覆盖结果；并行实验应指定不同输出目录。

## 8. 服务端指标与实时观察

另开一个 Bash 终端，设置相同的服务地址后运行：

```bash
export BASE_URL="http://127.0.0.1:8000"
INTERVAL=0.5 ./observe_live.sh
```

按 `Ctrl+C` 停止，CSV 保存在 `results/live/`。短测试可设置 `INTERVAL=0.1`；实际周期还包含抓取和处理耗时。记录 running、waiting、KV 使用率、累计输入/输出 token 和累计抢占次数。该脚本仅匹配代码中指定名称且带标签的指标；缺失指标可能显示 0，需对照原始 `/metrics` 确认。

手工采集、汇总示例：

```bash
./capture_metrics.sh before-custom
REQUESTS=40 CONCURRENCY=8 ./run_baseline.sh
./capture_metrics.sh after-custom
python3 summarize_metrics.py \
  --before results/metrics/latest-before-custom.prom \
  --after results/metrics/latest-after-custom.prom
```

可附加 `--client-summary results/baseline/summary-实际时间.json`，一起打印客户端摘要。快照保存为 `<标签>-<UTC时间>.prom`，同时更新 `latest-<标签>.prom`；差分摘要只打印到终端。

摘要中的延迟是服务端直方图 `_sum` / `_count` 增量得到的均值，不是客户端 P95。快照末态通常已无在飞请求，不能代表运行中的峰值。汇总会合并不同标签的同名指标，不隔离模型或其他客户端；其中 HTTP 请求计数也不一定等于本轮推理请求数。尽量使用无其他流量的服务，且不要在前后快照之间重启服务。

缺失指标可能显示 0 或 `n/a`；未发现 LMCache 原生指标不等于缓存未启用。需要确认快照实际暴露的指标，再判断缓存命中情况。

GPU/CPU 可在服务所在机器上另行观察（需安装对应工具）：

```bash
nvidia-smi -l 1
htop
```

## 9. LMCache 对照实验

保持模型、输入文件、请求数、并发、输出上限与其他服务配置一致，分别记录未启用 LMCache、启用后的冷状态、启用后的预热状态。服务配置变更和缓存清理需在服务端完成，本工具不会自动执行。

对比客户端 TTFT、TPOT、吞吐、失败率，以及服务端外部缓存查询/命中和本地前缀缓存指标。仅凭重复请求 TTFT 降低，不能单独证明 LMCache 生效；本地前缀缓存也可能复用输入。阶梯测试不同档位共享服务缓存，后续档位可能受前面请求预热影响，应记录缓存状态。

可使用 `record-template.md` 记录模型、vLLM/LMCache 版本、GPU、并行方式、调度参数、缓存后端、实验配置与结论。

## 10. 常见问题

| 现象 | 检查方式 |
|---|---|
| 提示 `Set MODEL` | 执行 `export MODEL="实际模型ID"`，或直接客户端传 `--model` |
| 连接失败、404 | 检查地址、端口、路由，以及是否误把 `/v1` 加进 `BASE_URL` |
| 401/403 | 当前客户端没有 API key 参数，也不会读取 `OPENAI_API_KEY`；鉴权服务需先扩展请求头支持 |
| observed 脚本开始就退出 | 检查 `/metrics` 是否可访问；可先用普通基线或直接 Python 测试 |
| 长前缀请求返回 400 | 检查服务日志中的上下文长度或请求参数错误，适当降低 `PREFIX_REPEATS` |
| token/TPOT 为空 | 检查服务是否返回流式 usage，及实际输出是否超过一个 token |
| `stream ended without a generated token` | 流中没有客户端识别的非空 content/reasoning_content，检查服务响应格式 |
| 脚本结束但有请求失败 | 查看汇总 `failed` 和 CSV `error`，必要时结合服务日志；脚本没有自动重试 |
| 修改 `MAX_TOKENS` 对混合/前缀测试无效 | 修改 JSONL 中每行的 `max_tokens`，再直接调用客户端 |
| `Permission denied` 或 Bash 出现 `\r` 错误 | 运行 `chmod +x *.sh`，并将脚本换行格式保存为 LF |
