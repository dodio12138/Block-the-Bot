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
scripts/run_tests.sh      # 编译 C++ 核心 + 9 项规则测试 + 11 项 Agent 测试
```

```bash
scripts/run_game.sh       # 启动游戏；客户端会自动拉起 agent/server.py
```

- 默认 Godot 路径是 Steam 版，可用 `GODOT=/path/to/Godot` 覆盖；Python 可用 `TBT_PYTHON` 覆盖。
- 快捷键：`I O T S Z J L` 出题，`N` 新对局，`F1` 开发者模式，`Esc` 退出回放。

### 接入真实的 Qwen3-0.6B

Agent 服务调用任意 OpenAI 兼容接口（默认 `http://127.0.0.1:8080/v1`）。本机目前**没有**安装推理运行时，HF 缓存里的 Qwen3-0.6B 也只有空目录。接入方式之一：

```bash
llama-server -m Qwen3-0.6B-Q8_0.gguf --port 8080
```

或者设置 `TBT_LLM_URL` / `TBT_LLM_MODEL` 指向 LM Studio、Ollama 的 `/v1` 接口。服务离线时，「0.6B 直接决策 / 分层策略」会自动回退到启发式，并在面板和日志中标记为 `fallback`。

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

## 目录

```
core/        C++17 规则核心、C API、无头 CLI（CMake）
agent/       Python Agent 服务：core.py 绑定、protocol、llm、policies、memory、session、server
game/        Godot 4.7 客户端（UI 全部由 scripts/main.gd 代码构建）
tests/       core/test_core.cpp、agent/test_agent.py（内含假 LLM 服务器）
experiments/ calibrate.py、mock_llm.py
data/        challenges.json；运行时生成 logs/、replays/、leaderboard.json
docs/        protocol.md
```

## 后续（文档 P4–P5）

- 训练：用核心 + 束搜索教师导出 `(state, candidates, teacher_action)`，再做 SFT。
- 系统性消融：L0/L1/L2、K = 4/8/16、每块调用 vs 每 3/5 块更新策略。
- 回放分享 / 「挑战同一序列」、限时出题模式。
