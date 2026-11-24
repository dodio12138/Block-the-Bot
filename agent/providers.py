"""Model providers: embedded worker, local OpenAI-compatible server, or cloud APIs (DeepSeek, OpenAI, ...).

Configuration lives OUTSIDE the repo in ~/.block-the-bot/llm.json (chmod 600). API keys are only written there
when the player ticks "remember"; otherwise they stay in memory. Environment variables (e.g. DEEPSEEK_API_KEY)
always work and are never written to disk. Keys are never logged or sent back to the game client.

Model names change over time — the defaults below are only starting points; after a key is entered the game
lists the provider's real models via GET /models.
"""
from __future__ import annotations

import json
import os
import stat
import urllib.request
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(os.environ.get("TBT_CONFIG", Path.home() / ".block-the-bot" / "llm.json"))

PRESETS: dict[str, dict] = {
    "embedded": {"label": "游戏内嵌（Qwen3-0.6B + LoRA）", "kind": "embedded"},
    "local": {"label": "本地服务（llama-server / LM Studio / Ollama）", "kind": "local",
              "base_url": "http://127.0.0.1:8080/v1", "model": "qwen3-0.6b", "key_env": ""},
    "deepseek": {"label": "DeepSeek", "kind": "cloud", "base_url": "https://api.deepseek.com",
                 "model": "deepseek-v4-flash", "key_env": "DEEPSEEK_API_KEY"},
    "openai": {"label": "OpenAI", "kind": "cloud", "base_url": "https://api.openai.com/v1",
               "model": "", "key_env": "OPENAI_API_KEY"},
    "qwen": {"label": "通义千问（DashScope）", "kind": "cloud",
             "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "", "key_env": "DASHSCOPE_API_KEY"},
    "moonshot": {"label": "Kimi（Moonshot）", "kind": "cloud", "base_url": "https://api.moonshot.cn/v1",
                 "model": "", "key_env": "MOONSHOT_API_KEY"},
    "zhipu": {"label": "智谱 GLM", "kind": "cloud", "base_url": "https://open.bigmodel.cn/api/paas/v4",
              "model": "", "key_env": "ZHIPUAI_API_KEY"},
    "custom": {"label": "自定义（OpenAI 兼容接口）", "kind": "cloud", "base_url": "", "model": "",
               "key_env": "TBT_API_KEY"},
}


def load_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_config(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 600: the file may hold API keys
    tmp.replace(CONFIG_PATH)


def resolve(provider: str, cfg: Optional[dict] = None, session_keys: Optional[dict] = None) -> dict:
    """Effective settings for a provider: preset <- saved config <- environment / in-memory key."""
    cfg = cfg if cfg is not None else load_config()
    preset = dict(PRESETS.get(provider, PRESETS["custom"]))
    saved = (cfg.get("providers") or {}).get(provider, {})
    out = {"provider": provider, "kind": preset["kind"], "label": preset["label"],
           "base_url": saved.get("base_url") or preset.get("base_url", ""),
           "model": saved.get("model") or preset.get("model", ""),
           "key_env": preset.get("key_env", "")}
    key = ""
    if out["key_env"] and os.environ.get(out["key_env"]):
        key, out["key_source"] = os.environ[out["key_env"]], "env"
    elif session_keys and session_keys.get(provider):
        key, out["key_source"] = session_keys[provider], "memory"
    elif saved.get("api_key"):
        key, out["key_source"] = saved["api_key"], "file"
    else:
        out["key_source"] = ""
    out["api_key"] = key
    return out


def public_view(cfg: Optional[dict] = None, session_keys: Optional[dict] = None) -> list[dict]:
    """Provider list for the game UI — never includes key material."""
    cfg = cfg if cfg is not None else load_config()
    rows = []
    for pid in PRESETS:
        r = resolve(pid, cfg, session_keys)
        rows.append({"id": pid, "label": r["label"], "kind": r["kind"], "base_url": r["base_url"], "model": r["model"],
                     "key_env": r["key_env"], "has_key": bool(r["api_key"]), "key_source": r["key_source"]})
    return rows


def list_models(base_url: str, api_key: str, timeout: float = 8.0) -> tuple[list[str], str]:
    """GET {base}/models — returns (model ids, error)."""
    req = urllib.request.Request(base_url.rstrip("/") + "/models",
                                 headers={"Authorization": f"Bearer {api_key}"} if api_key else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return [], describe_http_error(e.code)
    except Exception as e:
        return [], f"{type(e).__name__}: {e}"
    ids = [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)]
    return [i for i in ids if i], ""


def describe_http_error(code: int) -> str:
    return {401: "API Key 无效（401）", 402: "账户余额不足（402）", 403: "没有权限（403）",
            404: "地址或模型不存在（404）", 429: "请求太频繁或额度用完（429）"}.get(code, f"HTTP {code}")
