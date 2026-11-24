# 游戏内嵌模型

Agent 服务启动时，如果发现 `game/bin/tbt_ai_worker` 和这里的模型，就自动用游戏内嵌推理（进程内 llama.cpp，只用 CPU），不需要另外开 llama-server。模型文件不进 git。

| 文件 | 内容 | 来源 / 许可证 |
|---|---|---|
| `Qwen3-0.6B-Q8_0.gguf` | 底模（约 640 MB） | [Qwen/Qwen3-0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF)，Apache-2.0 |
| `bot-lora-v1.gguf` | 防守者 LoRA（约 20 MB） | 本项目训练（`training/`），转换方法见 `scripts/convert_lora.sh` |

规则：
- 文件名里不含 `lora` 的 `.gguf` 当作底模，优先选 Q8；
- 含 `lora` 的取文件名排序最后的一个（例如 v2 优先于 v1）。

也可以用环境变量指定：`TBT_MODEL=<底模>`、`TBT_LORA=<适配器>`；`TBT_LORA=none` 表示不加载适配器。
