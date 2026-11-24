# 在 Kaggle 上训练（免费 T4）

流程沿用 FromLev 的 `ai/train/kaggle`。

1. 打包数据集：`scripts/pack_kaggle.sh` 会生成 `training/kaggle/dataset/`，里面是 `train.jsonl`、`val.jsonl`、`train_lora.py`。
   把它上传为**私有**数据集 `block-the-bot-sft`；以后重新生成数据，就上传一个新版本。
2. 新建笔记本，导入 `training/kaggle/block_the_bot_lora.ipynb`；Settings 里 Accelerator 选 GPU T4 x2，打开 Internet；Add Input 加上这个数据集；然后 Save Version → Save & Run All。
3. 在 Output 里下载 `block-the-bot-lora.zip`，解压到 `training/out/`，然后在本机转成 GGUF：
   `scripts/convert_lora.sh training/out/adapter models/bot-lora.gguf`
4. 评测：
   `scripts/start_llm.sh --lora models/bot-lora.gguf`
   `python3 training/eval_offline.py --data data/sft/v1/test.jsonl`
