#!/usr/bin/env python3
"""Block the Bot 防守者的 LoRA 微调（Qwen3-0.6B）。改编自 FromLev/ai/train/train_lora.py。

数据：training/gen_teacher_data.py 生成的 chat-JSONL（提示词与游戏发给模型的一字不差，答案是候选 ID）：
    python3 training/gen_teacher_data.py --games 60 --out data/sft/v1
训练（沿用 FromLev 的训练环境 torch 2.2.2 / transformers 4.51.3 / peft 0.15.2）：
    scripts/train_lora.sh --level L2 --out training/out
转成 GGUF 适配器（llama-server 用 --lora 加载在底模上）：
    scripts/convert_lora.sh training/out/adapter models/bot-lora.gguf
评测：scripts/start_llm.sh --lora models/bot-lora.gguf，然后运行 training/eval_offline.py。

要点：
  · 提示词使用模型自带的对话模板并关掉思考（enable_thinking=False），与 llama-server 的设置一致；
  · 只在助手输出（候选 ID + 结束符）上计算损失；
  · 只训练 LoRA（注意力和 MLP 的投影层），底模不动；CPU / MPS 用 float32，CUDA 用混合精度；
  · 随机种子固定，结果可复现。
"""
import argparse
import json
import math
import os
import random
import sys
import time


def main():
    ap = argparse.ArgumentParser(description="Block the Bot 防守者 LoRA 微调")
    ap.add_argument("--data", required=True, help="gen_teacher_data.py 生成的 train.jsonl")
    ap.add_argument("--val-data", default="", help="单独的验证集（不给就从 --data 里切 --val 比例）")
    ap.add_argument("--level", default="", help="只用某一档协议的样本（L1 / L2），空 = 全部")
    ap.add_argument("--device", default="auto", help="auto | cpu | mps | cuda")
    ap.add_argument("--base", default="Qwen/Qwen3-0.6B", help="底模（Hugging Face 格式的目录，或仓库名 Qwen/Qwen3-0.6B）")
    ap.add_argument("--out", default="training/out", help="输出目录（adapter/ 是最好的一版）")
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--alpha", type=int, default=32)
    ap.add_argument("--dropout", type=float, default=0.05)
    ap.add_argument("--batch", type=int, default=8, help="每步的句数（梯度累积凑够）")
    ap.add_argument("--micro", type=int, default=4, help="一次前向的句数（内存够就大一点）")
    ap.add_argument("--max-len", type=int, default=768, help="超过的截掉（提示词太长说明没用 --compact）")
    ap.add_argument("--val", type=float, default=0.05, help="留出验证的比例（看过拟合）")
    ap.add_argument("--threads", type=int, default=max(1, (os.cpu_count() or 8) // 2), help="默认按物理核（超线程不算）")
    ap.add_argument("--limit", type=int, default=0, help="只用前 N 条（先试跑）")
    ap.add_argument("--check", type=int, default=100, help="训练后在验证集上实际生成 N 句，看与教师一致几句（0 = 不做）")
    ap.add_argument("--max-steps", type=int, default=0, help="最多训练多少步（试跑测速用，0 = 不限）")
    ap.add_argument("--seed", type=int, default=1234)
    args = ap.parse_args()

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    if args.device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    else:
        device = args.device
    cuda = device == "cuda"
    # 权重一律 float32（LoRA 只有 1% 参数要训，显存够）；显卡上用混合精度算：支持 bfloat16 的（A100、4090……）用它，
    # T4 这类老卡（Turing）不支持 bfloat16，用 float16 + 梯度缩放
    amp = None
    if cuda:
        # 新版 PyTorch 在 T4 上也报「支持 bfloat16」（模拟的，慢）：按算力判断，8.0 以上（Ampere 起）才有原生 bfloat16
        amp = torch.bfloat16 if torch.cuda.get_device_capability(0)[0] >= 8 else torch.float16
    # torch 2.3 起是 torch.amp.GradScaler("cuda")；本机的 2.2 只有 torch.cuda.amp.GradScaler
    scaler = torch.amp.GradScaler("cuda", enabled=amp == torch.float16) if hasattr(torch.amp, "GradScaler") else torch.cuda.amp.GradScaler(enabled=amp == torch.float16)
    from contextlib import nullcontext
    autocast = (lambda: torch.autocast("cuda", dtype=amp)) if amp else nullcontext

    def load(path):
        out = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
        if args.level:
            out = [r for r in out if r.get("meta", {}).get("level") == args.level]
        return out

    rows = load(args.data)
    random.shuffle(rows)
    if args.limit:
        rows = rows[: args.limit]
    if args.val_data:
        train_rows = rows
        val_rows = load(args.val_data)
        random.shuffle(val_rows)
        val_rows = val_rows[: max(args.check, 200)]
    else:
        n_val = max(1, int(len(rows) * args.val)) if args.val > 0 else 0
        val_rows, train_rows = rows[:n_val], rows[n_val:]
    print(f"数据 {len(rows)} 条：训练 {len(train_rows)}，验证 {len(val_rows)}；设备 {device}，{args.threads} 线程", flush=True)

    tok = AutoTokenizer.from_pretrained(args.base)
    end = tok.convert_tokens_to_ids("<|im_end|>")

    def encode(r):
        msgs = r["messages"]
        prompt = tok.apply_chat_template(msgs[:-1], tokenize=True, add_generation_prompt=True, enable_thinking=False)
        target = tok(msgs[-1]["content"], add_special_tokens=False)["input_ids"] + [end]
        ids = (prompt + target)[: args.max_len]
        labels = ([-100] * len(prompt) + target)[: args.max_len]
        return {"ids": ids, "labels": labels, "prompt": prompt, "answer": msgs[-1]["content"]}

    train = [encode(r) for r in train_rows]
    val = [encode(r) for r in val_rows]
    lens = [len(x["ids"]) for x in train]
    print(f"每条平均 {sum(lens) / len(lens):.0f} 个 token，最长 {max(lens)}", flush=True)
    if max(lens) >= args.max_len:
        print(f"警告：有的句子超过 --max-len {args.max_len} 被截断（导出时加 --compact）", flush=True)

    model = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=torch.float32).to(device)
    if cuda:
        print(f"显卡 {torch.cuda.get_device_name(0)}，混合精度 {amp}", flush=True)
    model.config.use_cache = False
    cfg = LoraConfig(r=args.rank, lora_alpha=args.alpha, lora_dropout=args.dropout, bias="none", task_type="CAUSAL_LM",
                     target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"])
    model = get_peft_model(model, cfg)
    model.print_trainable_parameters()

    def batches(data, size, shuffle):
        idx = list(range(len(data)))
        if shuffle:
            random.shuffle(idx)
            # 长度相近的放一批（少补齐）：按块排序
            blocks = [sorted(idx[i:i + size * 16], key=lambda k: len(data[k]["ids"])) for i in range(0, len(idx), size * 16)]
            idx = [k for b in blocks for k in b]
        out = [idx[i:i + size] for i in range(0, len(idx), size)]
        if shuffle:
            random.shuffle(out)
        return out

    def collate(items):
        # 左边补齐：每句的答案都落在最后几个位置上，只算那几个位置的 logits（词表 15 万，全算既占显存又慢）
        n = max(len(x["ids"]) for x in items)
        pad = tok.pad_token_id if tok.pad_token_id is not None else end
        ids = torch.full((len(items), n), pad, dtype=torch.long)
        lab = torch.full((len(items), n), -100, dtype=torch.long)
        att = torch.zeros((len(items), n), dtype=torch.long)
        for i, x in enumerate(items):
            L = len(x["ids"])
            ids[i, n - L:] = torch.tensor(x["ids"])
            lab[i, n - L:] = torch.tensor(x["labels"])
            att[i, n - L:] = 1
        return ids.to(device), lab.to(device), att.to(device)

    def answer_loss(ids, lab, att):
        """只在答案（labels 不是 -100）上算的交叉熵：位置 t 的 logits 预测 t+1 的 token。"""
        n = ids.shape[1]
        first = int((lab != -100).float().argmax(dim=1).min())   # 最早的答案 token 位置
        k = n - first + 1                                        # 要算 logits 的末尾位置数（含答案前一个）
        pos = (att.cumsum(1) - 1).clamp(min=0)                   # 左补齐时的位置编号
        out = model(input_ids=ids, attention_mask=att, position_ids=pos, logits_to_keep=k)
        logits = out.logits[:, :-1].float()
        target = lab[:, n - k + 1:]
        return torch.nn.functional.cross_entropy(logits.reshape(-1, logits.shape[-1]), target.reshape(-1), ignore_index=-100)

    def val_loss():
        if not val:
            return float("nan")
        model.eval()
        tot, n = 0.0, 0
        with torch.no_grad():
            for b in batches(val, args.micro, False):
                ids, lab, att = collate([val[k] for k in b])
                with autocast():
                    l = answer_loss(ids, lab, att)
                tot += l.item() * len(b)
                n += len(b)
        model.train()
        return tot / max(n, 1)

    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    accum = max(1, args.batch // args.micro)
    steps_per_epoch = math.ceil(len(train) / (args.micro * accum))
    total = max(1, int(steps_per_epoch * args.epochs))
    if args.max_steps:
        total = min(total, args.max_steps)
    warm = max(1, total // 20)
    sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / warm) * max(0.0, (total - s) / max(1, total - warm)))
    os.makedirs(args.out, exist_ok=True)
    print(f"共 {total} 步（每步 {args.micro * accum} 句），先看一次验证损失：{val_loss():.4f}", flush=True)

    best, step, t0 = float("inf"), 0, time.time()
    model.train()
    while step < total:
        mbs = batches(train, args.micro, True)
        for i in range(0, len(mbs), accum):
            if step >= total:
                break
            loss_sum = 0.0
            for b in mbs[i:i + accum]:
                ids, lab, att = collate([train[k] for k in b])
                with autocast():
                    loss = answer_loss(ids, lab, att) / accum
                scaler.scale(loss).backward()
                loss_sum += loss.item()
            scaler.unscale_(opt)
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            scaler.step(opt)
            scaler.update()
            sched.step()
            opt.zero_grad(set_to_none=True)
            step += 1
            if step % 10 == 0 or step == 1:
                el = time.time() - t0
                print(f"步 {step}/{total}  损失 {loss_sum:.4f}  学习率 {sched.get_last_lr()[0]:.2e}  已用 {el / 60:.1f} 分钟，"
                      f"还要约 {el / step * (total - step) / 60:.0f} 分钟", flush=True)
            if step % max(1, steps_per_epoch // 2) == 0 or step == total:
                vl = val_loss()
                print(f"  验证损失 {vl:.4f}", flush=True)
                if vl < best:
                    best = vl
                    model.save_pretrained(os.path.join(args.out, "adapter"))
                    print(f"  存下（目前最好）→ {args.out}/adapter", flush=True)
    if not val:
        model.save_pretrained(os.path.join(args.out, "adapter"))

    # 训练后实际生成看看：验证集上输出合法（是数字）几句、与教师一致几句
    if args.check and val:
        model.eval()
        ok, legal = 0, 0
        sample = val[: args.check]
        for x in sample:
            ids = torch.tensor([x["prompt"]], device=device)
            with torch.no_grad(), autocast():
                out = model.generate(input_ids=ids, attention_mask=torch.ones_like(ids), max_new_tokens=6, do_sample=False,
                                     eos_token_id=end, pad_token_id=end)
            text = tok.decode(out[0][len(x["prompt"]):], skip_special_tokens=True).strip()
            text = text.split("</think>")[-1].strip()
            legal += text.isdigit()
            ok += text == x["answer"].strip()
        print(f"验证集实际生成：合法 {legal}/{len(sample)}，与教师一致 {ok}/{len(sample)}", flush=True)
    json.dump(vars(args) | {"best_val_loss": best}, open(os.path.join(args.out, "train_args.json"), "w"), ensure_ascii=False, indent=2)
    print(f"完成。适配器：{args.out}/adapter（下一步转 GGUF，见文件头）", flush=True)


if __name__ == "__main__":
    sys.exit(main())
