#!/usr/bin/env python3
"""Save an API key for a provider into ~/.block-the-bot/llm.json (chmod 600) without printing it.

    pbpaste | python3 scripts/set_api_key.py deepseek          # macOS: key from the clipboard
    python3 scripts/set_api_key.py deepseek                     # prompts (input hidden)

Optional: --model <name> to pin a model, --no-select to keep the current provider.
Then it checks the key with GET /models and prints the model list (never the key).
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.providers import PRESETS, list_models, load_config, resolve, save_config  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("provider", choices=[p for p, v in PRESETS.items() if v["kind"] == "cloud"])
    ap.add_argument("--model", default="")
    ap.add_argument("--base-url", default="")
    ap.add_argument("--no-select", action="store_true", help="save the key but keep the current provider")
    a = ap.parse_args()

    key = sys.stdin.readline().strip() if not sys.stdin.isatty() else getpass.getpass("API Key: ").strip()
    if not key or any(c.isspace() for c in key) or len(key) < 16:
        print("没有读到有效的 API Key（剪贴板里应该只有 Key 本身）。", file=sys.stderr)
        return 1
    cfg = load_config()
    entry = cfg.setdefault("providers", {}).setdefault(a.provider, {})
    entry["api_key"] = key
    if a.model:
        entry["model"] = a.model
    if a.base_url:
        entry["base_url"] = a.base_url
    if not a.no_select:
        cfg["provider"] = a.provider
    save_config(cfg)
    r = resolve(a.provider, cfg)
    print(f"已保存 {PRESETS[a.provider]['label']} 的 Key（{len(key)} 个字符，以 …{key[-4:]} 结尾）"
          f"到 ~/.block-the-bot/llm.json（权限 600）。")
    models, err = list_models(r["base_url"], key)
    if err:
        print(f"连接检查失败：{err}")
        return 2
    print(f"连接正常，账号下可用模型：{', '.join(models) or '（接口没有返回列表）'}")
    if models and r["model"] not in models:
        print(f"注意：当前模型 {r['model']} 不在列表里。可以重新运行并加 --model <上面任一名字>，或在游戏「模型设置…」里选择。")
    else:
        print(f"当前模型：{r['model']}。启动游戏后选「普通」难度或「模型直接决策」即可使用。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
