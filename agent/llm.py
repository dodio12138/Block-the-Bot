"""OpenAI-compatible chat client: local servers (llama-server, LM Studio, Ollama /v1) and cloud APIs
(DeepSeek, OpenAI, DashScope, Moonshot, Zhipu, custom).

Latency is measured here, server side; the model never reports its own timing.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

DEFAULT_URL = os.environ.get("TBT_LLM_URL", "http://127.0.0.1:8080/v1")
DEFAULT_MODEL = os.environ.get("TBT_LLM_MODEL", "qwen3-0.6b")
DEFAULT_TIMEOUT = float(os.environ.get("TBT_LLM_TIMEOUT", "12"))


@dataclass
class LLMResult:
    ok: bool
    text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tokens_estimated: bool = False
    latency_ms: float = 0.0
    error: str = ""


class LLMClient:
    """kind="local": llama-server & co. (GBNF grammar, Qwen3 no-think switch).
    kind="cloud": DeepSeek / OpenAI / ... — standard fields only, Bearer key, JSON mode instead of grammar."""

    def __init__(self, base_url: str = DEFAULT_URL, model: str = DEFAULT_MODEL, timeout_s: float = DEFAULT_TIMEOUT,
                 api_key: str = "", kind: str = "local", provider: str = "local", label: str = ""):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self.api_key = api_key
        self.kind = kind
        self.provider = provider
        self.label = label
        self.supports_grammar = kind == "local"  # llama-server accepts a GBNF "grammar" field
        self._avail: Optional[bool] = None
        self._avail_checked = 0.0
        self.served_model = ""
        self.error = ""

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def available(self, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and self._avail is not None and now - self._avail_checked < (15.0 if self._avail else 5.0):
            return self._avail
        self._avail_checked = now
        if self.kind == "cloud" and not self.api_key:
            self._avail, self.error = False, "还没有填 API Key"
            return False
        req = urllib.request.Request(self.base_url + "/models", headers=self._headers())
        try:
            with urllib.request.urlopen(req, timeout=0.8 if self.kind == "local" else 6.0) as r:
                data = json.loads(r.read().decode() or "{}")
                models = [m.get("id", "") for m in data.get("data", []) if isinstance(m, dict)]
                if self.kind == "cloud":
                    self.served_model = self.model if (not models or self.model in models) else ""
                    if not self.served_model:
                        self._avail, self.error = False, f"账号下没有模型 {self.model}"
                        return False
                else:
                    self.served_model = models[0] if models else ""
                self._avail, self.error = True, ""
        except urllib.error.HTTPError as e:
            from .providers import describe_http_error
            self._avail, self.error = False, describe_http_error(e.code)
        except Exception as e:
            self._avail, self.error = False, f"{type(e).__name__}: {e}"
        return self._avail

    def status(self) -> dict:
        # never include the API key
        return {"url": self.base_url, "backend": self.kind, "provider": self.provider, "label": self.label,
                "model": self.model, "served_model": self.served_model, "available": bool(self._avail),
                "error": self.error}

    def chat(self, system: str, user: str, max_tokens: int = 8, grammar: Optional[str] = None,
             json_mode: bool = False) -> LLMResult:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
        if self.kind == "local":
            # Qwen3: disable the thinking block (llama-server forwards these to the chat template).
            body["chat_template_kwargs"] = {"enable_thinking": False}
            if grammar:
                body["grammar"] = grammar
        else:
            # cloud: plain OpenAI fields; drop the local-model switch; give reasoning models room to think
            body["messages"][1]["content"] = user.replace(" /no_think", "")
            body["max_tokens"] = max(max_tokens, 1024 if _is_reasoning(self.model) else 64)
            if json_mode:
                body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(),
                                     headers=self._headers(), method="POST")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            from .providers import describe_http_error
            self.error = describe_http_error(e.code)
            if e.code in (401, 402, 403):
                self._avail = False
            return LLMResult(False, latency_ms=(time.perf_counter() - t0) * 1000, error=self.error)
        except Exception as e:  # timeout, refused
            self._avail = False if isinstance(e, (urllib.error.URLError, ConnectionError)) else self._avail
            return LLMResult(False, latency_ms=(time.perf_counter() - t0) * 1000, error=f"{type(e).__name__}: {e}")
        ms = (time.perf_counter() - t0) * 1000
        try:
            text = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError):
            return LLMResult(False, latency_ms=ms, error="malformed response")
        usage = data.get("usage") or {}
        pt, ct = usage.get("prompt_tokens"), usage.get("completion_tokens")
        est = pt is None or ct is None
        if est:
            pt, ct = estimate_tokens(system + user), estimate_tokens(text)
        return LLMResult(True, text, int(pt), int(ct), est, ms)


def _is_reasoning(model: str) -> bool:
    m = model.lower()
    return any(k in m for k in ("reasoner", "-r1", "think", "o1", "o3", "o4"))


def estimate_tokens(s: str) -> int:
    return max(1, round(len(s) / 3.2))


_THINK = re.compile(r"<think>.*?</think>", re.S)


def strip_think(text: str) -> str:
    text = _THINK.sub("", text)
    return text.split("</think>")[-1].strip()


def parse_action_id(text: str) -> Optional[int]:
    t = strip_think(text)
    m = re.search(r'"action_id"\s*:\s*(-?\d+)', t)
    if m:
        return int(m.group(1))
    m = re.search(r"-?\d+", t)
    return int(m.group(0)) if m else None


def parse_strategy(text: str) -> Optional[dict]:
    t = strip_think(text)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None
