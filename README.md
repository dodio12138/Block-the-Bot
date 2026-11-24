# Block the Bot — Tetris Adversarial Agent 原型

人类当**出题者**指定方块顺序，0.6B 语言模型（或传统 AI）当**防守者**选择落点。
设计依据：《Tetris Adversarial Agent 技术设计文档 v0.2》。

```
Godot 4.7 客户端 (game/)  ──TCP JSON-lines──▶  Python Agent 服务 (agent/)  ──ctypes──▶  C++ 规则核心 (core/)
  出题 / 动画 / 面板                              会话·策略·记忆·日志·回放                SRS·可达落点 BFS·特征·评估·回放校验
                                                        │
                                                        └─HTTP (OpenAI 兼容)─▶ Qwen3-0.6B（llama-server 等）
```

## 快速开始

```bash
scripts/run_tests.sh      # 编译 C++ 核心（含内嵌推理进程）+ 10 项规则测试 + 18 项 Agent 测试
```

```bash
scripts/run_game.sh       # 启动游戏；客户端会自动拉起 agent/server.py
```

- 默认 Godot 路径是 Steam 版，可用 `GODOT=/path/to/Godot` 覆盖；Python 可用 `TBT_PYTHON` 覆盖。
- 快捷键：`I O T S Z J L` 出题，`N` 新对局，`F1` 开发者模式，`Esc` 退出回放。

### 模型与推理内嵌在游戏里

游戏自带推理进程 `game/bin/tbt_ai_worker`（复制改编自 FromLev 的 `sd_ai_worker`）。它把 llama.cpp b11700 静态链接进去，只用 CPU，模型放在 `game/models/`。Agent 服务启动时只要发现这两样，就自动用内嵌推理，不需要另外开任何服务：

```bash
cmake --build core/build --target tbt_ai_worker    # 第一次编译 llama.cpp 需要几分钟
scripts/run_game.sh
```

模型文件不进 git，放法见 [game/models/README.md](game/models/README.md)。底模是 Qwen3-0.6B-Q8_0（Apache-2.0），适配器是本项目训练的 `bot-lora-v1.gguf`。

「0.6B 直接决策」用 GBNF 约束解码：模型只能输出当前候选里的 ID。「分层策略」同样只能输出合法的策略 JSON。Agent 服务仍然会再校验一遍；推理进程崩溃或超时时，自动回退到启发式并记录。

也可以改用外部的 OpenAI 兼容接口，例如 llama-server、LM Studio 或 Ollama：

```bash
scripts/start_llm.sh --lora game/models/bot-lora-v1.gguf
TBT_LLM=http scripts/run_game.sh
```

不接模型也能测试整条 LLM 管线：

```bash
python3 experiments/mock_llm.py --port 8080   # 随机作答的假模型，偶尔故意输出非法 ID
```

## 已实现（对应设计文档）

| 文档 | 实现 |
|---|---|
| §3 规则核心 | `core/`：10×20 + 4 行隐藏区、7 种方块、SRS 旋转与踢墙、Hold、Block-out / Lock-out 判负（可选 any-above）、7-Bag 约束、确定性哈希 |
| §3 候选生成 | 对 左/右/软降/顺转/逆转 做 BFS，包含踢墙、tuck、T-Spin 入槽；按格子集合去重；每个候选附带完整可执行路径（测试逐步验证合法性） |
| §5 特征 | 列高、最高、总高、洞、起伏、累计井深、最深井、行/列转换、落点高度、顶部风险、综合风险 |
| §5.3/5.4 协议 | `agent/protocol.py`：紧凑候选协议，L0 / L1 / L2 Top-K（一半取启发式最优，一半按 rot/x 多样性补齐），候选顺序打乱，局部 ID 映射回核心 ID |
| §6.1 Direct Agent | 每块调用一次模型，输出 ID → 校验 → 执行；非法输出、超时、离线时回退 |
| §6.2 Hierarchical | 模型输出 `{"strategy","weights","valid_for"}`，经限幅后映射为评估权重；本地控制器每块执行 argmax Q(s,a;w)；在过期、风险突变、越过高度阈值、执行失败时重新规划 |
| §7/§8 事件与接口 | PieceSpawned / PieceLocked / LinesCleared / BoardRiskChanged / StrategyExpired / ExecutionFailed / GameOver；校验 state_version，推理耗时由服务端测量 |
| §9 适应性 | 对手模型：最近 N 块、频率偏好、7-Bag 中「留到袋末」的习惯；长期统计写入 `data/opponent_profile_*.json` |
| §1.2 玩法 | 自由出题、7-Bag 对抗（AI 存活 150 块获胜）、5 个挑战关卡、压力提示、风险增量时间线、本地排行榜、对局回放（播放前由核心重新校验） |
| §11 指标 | 开发者模式：每块调用次数、回退率、P50/P95 延迟、Token 数、各来源占比，以及模型输入/输出原文 |

## 无头仿真与平衡

```bash
core/build/tbt_headless sim --games 20 --mode free --attacker adversarial
core/build/tbt_headless replay data/replays/<id>.tbr
python3 experiments/calibrate.py          # 用一步对抗出题者检验各关卡预算
```

基线启发式 AI 对一步对抗出题者的实测（每种配置 10–20 局，截至 300–400 块）：

| 配置 | AI 平均存活块数 |
|---|---|
| 7-Bag，AI 有 Hold | 不会 Top-out（达到 300–400 块上限） |
| 7-Bag，AI 无 Hold | ~121 |
| 自由出题，AI 有 Hold | ~77 |
| 自由出题，AI 无 Hold | ~39 |

因此「7-Bag 对抗」默认关闭 AI 的 Hold。

两步搜索教师（`--defender search`，可在游戏里选「两步搜索（教师）」）对同一个出题者的实测，每种配置 10 局：

| 配置 | 启发式 | 两步搜索 | 每块耗时 |
|---|---|---|---|
| 自由出题，AI 有 Hold | 75.8 | **131.8** | ~41 ms |
| 7-Bag，AI 无 Hold | 121.4 | **≥300**（10 局都没被推倒） | ~16 ms |

## 训练与评估（P5 起步）

两步搜索教师只使用公开信息：它并不知道下一块是什么，而是对出题者可能出的每一种方块都评估一遍，取最坏情况与平均情况的加权。

```bash
# 1) 教师数据：按局切分 train/val/test，候选顺序打乱，chat-JSONL 格式（可直接喂给常见 SFT 训练器）
python3 training/gen_teacher_data.py --games 60 --out data/sft/v1

# 2) 无训练基线：模型在测试集上的输出合法率、与教师的一致率、延迟和 Token，并附带无模型基线（启发式 / 总选第一个 / 随机）
python3 training/eval_offline.py --data data/sft/v1/test.jsonl --limit 300

# 3) 消融实验：策略 × 出题者 × 信息协议，与游戏走同一条会话代码路径
python3 experiments/ablation.py --configs heuristic,search,llm:L2:8,llm:L2:4,llm:L1,hier:5,hier:3 --attackers adversarial,random
```

### 训练（Kaggle T4，流程沿用 FromLev）

```bash
scripts/pack_kaggle.sh                       # 打包 L2 样本 + 训练脚本，上传为私有数据集 block-the-bot-sft
# 在 Kaggle 导入 training/kaggle/block_the_bot_lora.ipynb，选 GPU T4 x2，然后 Save & Run All
scripts/convert_lora.sh training/out/adapter models/bot-lora-v1.gguf
scripts/start_llm.sh --lora models/bot-lora-v1.gguf
```

本机推理用的是 llama.cpp 官方预编译版 b12651（macOS x64，放在 `~/tools/llama.cpp/`），模型是 Qwen3-0.6B-Q8_0（放在 `~/models/qwen3-0.6b/`）。训练环境、底模和 LoRA 转换脚本都直接复用 FromLev 里的（见 `scripts/train_lora.sh`、`scripts/convert_lora.sh`）。

### 结果：LoRA v1

v1 只用 L2 样本训练：6378 条，2 遍，r=16，在 T4 上约 28 分钟，验证损失从 4.05 降到 0.515。

离线评测：测试集 841 条 L2 样本，来自训练时没见过的对局。

| | 未训练 0.6B | **LoRA v1** | 启发式 | 随机 |
|---|---|---|---|---|
| 输出合法 ID | 0%（回答 `C` 之类） | **100%** | — | — |
| 与教师一致 | 0% | **71.7%** | 80.6% | 12.5% |
| 延迟（本机 CPU） | P50 1.45 s | **P50 1.08 s / P95 1.26 s** | <1 ms | — |

模型最常选的位置只占 14.7%（均匀分布是 12.5%），说明它没有靠固定位置投机。

游戏内对比：7-Bag，AI 无 Hold，每种配置 5 局，上限 150 块。

| 防守者 | 对抗型出题者：平均存活 | Top-out 率 | 随机出题者：平均存活 |
|---|---|---|---|
| 启发式 | 125.4 | 0.8 | 150（未倒） |
| **0.6B + LoRA v1** | **137.0** | 0.8 | 150（未倒） |
| 两步搜索（教师） | 150（未倒） | 0 | 150（未倒） |

在对抗型出题者下，0.6B 每块都调用模型（每块 1 次），没有出现回退，输出全部合法。

在随机出题者下（补测，超时上限 12 秒），输出合法率同样是 100%，但有 12% 的调用超时、回退到了启发式。原因是测试期间这台机器被其他程序占满（负载均值约 120）。llama-server 日志显示，机器空闲时每次请求平均约 1.03 秒，没有超过 4 秒的。所以统计时把「调用失败（超时）」和「输出非法」分开计数。

需要注意两点：
- 每种配置只有 5 局，样本很小；
- 对抗型出题者是按启发式的弱点来挑方块的，所以和启发式「不一样」的防守者天然占一点便宜。

## 目录

```
core/        C++17 规则核心、C API、无头 CLI（CMake）
agent/       Python Agent 服务：core.py 绑定、protocol、llm、policies、memory、session、server
game/        Godot 4.7 客户端（UI 全部由 scripts/main.gd 代码构建）
tests/       core/test_core.cpp、agent/test_agent.py（内含假 LLM 服务器）
experiments/ calibrate.py、mock_llm.py、ablation.py
training/    gen_teacher_data.py、eval_offline.py
data/        challenges.json；运行时生成 logs/、replays/、leaderboard.json
docs/        protocol.md
```

## 后续（文档 P4–P5）

- 安装推理与训练依赖后：跑无训练基线 → 用 LoRA 做 SFT → 跑完整消融。
- 回放分享 / 「挑战同一序列」、限时出题模式。
