# 游戏内嵌模型

Agent 服务启动时，如果发现 `game/bin/tbt_ai_worker` 和这里的模型，就自动用游戏内嵌推理（进程内 llama.cpp，只用 CPU），不需要另外开 llama-server。模型文件不进 git。

| 文件 | 内容 | 来源 / 许可证 |
|---|---|---|
| `Qwen3-0.6B-Q4_K_M.gguf` | 底模，默认（约 520 MB） | Qwen3-0.6B 的 Q4_K_M 量化（复用 FromLev 的文件），Apache-2.0 |
| `Qwen3-0.6B-Q8_0.gguf` | 底模，可选（约 640 MB） | [Qwen/Qwen3-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF)，Apache-2.0 |
| `bot-lora-v1.gguf` | 防守者 LoRA（约 20 MB） | 本项目训练（`training/`），转换方法见 `scripts/convert_lora.sh` |

规则：
- 文件名里不含 `lora` 的 `.gguf` 当作底模，优先选 Q4，其次 Q8。Q4 在本机 CPU 上比 Q8 快约 3 倍（P50 0.83 秒 vs 2.57 秒），测试集准确率相同（75.0% vs 73.5%）。
- 含 `lora` 的取文件名排序最后的一个（例如 v2 优先于 v1）。

也可以用环境变量指定：`TBT_MODEL=<底模>`、`TBT_LORA=<适配器>`；`TBT_LORA=none` 表示不加载适配器。
