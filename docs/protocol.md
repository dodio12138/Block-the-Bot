# 协议规范 v1

## 1. 客户端 ⇄ Agent 服务（TCP，每行一个 JSON）

默认地址 `127.0.0.1:47800`（环境变量 `TBT_PORT`）。每个连接对应一个会话。

| 方向 | type | 字段 |
|---|---|---|
| C→S | `hello` | `protocol_version` |
| C→S | `new_game` | `mode` (`free`/`bag7`/`challenge`)、`agent` (`heuristic`/`llm`/`hier`/`random`)、`hold`、`challenge_id`、`info_level` (`L0`/`L1`/`L2`)、`top_k` |
| C→S | `submit_piece` | `piece`、`state_version`（与当前版本不一致时拒绝） |
| C→S | `get_replay` | `which`（`last` 或文件名） |
| S→C | `hello` | `core_version`、`agents`、`challenges`、`llm` 状态 |
| S→C | `thinking` | `event:"PieceSpawned"`、`piece`、`candidate_count` |
| S→C | `decision` | `action_id`、`source`、`reason`、`cells`、`path`（逐步格子）、`lines`、`hold`、`hold_only`、`risk_before/after`、`pressure_delta`、`inference_ms`、`tokens_in/out`、`llm_called`、`fallback`、`prompt`、`raw`、`strategy`、`events`、`top_cells` |
| S→C | `state` | `game`（核心状态）、`session`（模式/预算/胜负/回放路径）、`pressure`（各方块 0..1）、`opponent`、`timeline`、`dev`（指标） |
| S→C | `replay` | `ok`、`hash`、`frames[]` |
| S→C | `error` | `message`，可附带 `state` |

`source` 取值：`heuristic` / `llm` / `fallback` / `hier-local` / `random`。`fallback` 表示这一步实际由启发式完成，统计时与模型的贡献分开计。

## 2. 模型输入（紧凑协议）

Direct（L2）：

```text
V=1;P=T;HOLD=I;H=0,0,1,2,2,1,0,0,0,0;HO=0;MX=2;RISK=0.06
OPP=SZ
#id:x,rot,hold,clear,holes,max,bump
C=0:3,2,0,0,0,3,6;1:0,1,1,0,0,4,9;...
OUT=ID /no_think
```

- `x` 为最左列，`hold=1` 表示使用 Hold。
- ID 是局部编号，由服务端映射回核心候选 ID 后再校验。
- 模型输出 `1` 或 `{"action_id":1}` 均可解析，会自动去掉 `<think>` 块。

分层策略：

```text
H=...;MX=6;HO=1;BUMP=7;WELL=2;RISK=0.22;LINES=3;PIECES=18
OPP=-;RECENT=SZSZT;PREV=survival
OUT=JSON /no_think
```

期望输出：`{"strategy":"survival|dig|flat|clear|balanced","weights":[holes,height,bump,lines,wells],"valid_for":1-10}`。每个权重先限幅到 0–10，再换算为 0.1×–2× 的倍率作用在预设权重上。

## 3. 回放文件 `.tbr`

```text
# 注释行（可选）
TBR 1
rules <width> <visible> <hidden> <hold 0/1> <topout 0=lock_out 1=any_above> <mode 0=free 1=bag7>
board <hex 行，自底向上，覆盖全部高度>
P <piece>
A <candidate_id> <x> <y> <rot> <hold 0|1|2(仅存入 hold)>
...
```

重放时，每个 `A` 行都必须与重新生成的候选在 `x / y / rot / hold` 上完全一致，否则报 `replay_mismatch`。最终状态哈希用于检验确定性。

## 4. C API

见 `core/include/tbt/capi.h`。所有返回的字符串都归游戏句柄所有，在下一次调用前有效。
