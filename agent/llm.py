"""OpenAI-compatible chat client (llama.cpp `llama-server`, LM Studio, Ollama /v1 ...).

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
    def __init__(self, base_url: str = DEFAULT_URL, model: str = DEFAULT_MODEL, timeout_s: float = DEFAULT_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s
        self._avail: Optional[bool] = None
        self._avail_checked = 0.0
        self.served_model = ""

    def available(self, force: bool = False) -> bool:
        now = time.monotonic()
        if not force and self._avail is not None and now - self._avail_checked < 15.0:
            return self._avail
        self._avail_checked = now
        try:
            with urllib.request.urlopen(self.base_url + "/models", timeout=0.8) as r:
                data = json.loads(r.read().decode() or "{}")
                models = [m.get("id", "") for m in data.get("data", [])]
                self.served_model = models[0] if models else ""
                self._avail = True
        except Exception:
            self._avail = False
        return self._avail

    def status(self) -> dict:
        return {"url": self.base_url, "model": self.model, "served_model": self.served_model,
                "available": bool(self._avail)}

    supports_grammar = True  # llama-server accepts a GBNF "grammar" field

    def chat(self, system: str, user: str, max_tokens: int = 8, grammar: Optional[str] = None) -> LLMResult:
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
            # Qwen3: disable the thinking block (llama-server forwards these to the chat template).
            "chat_template_kwargs": {"enable_thinking": False},
        }
        if grammar:
            body["grammar"] = grammar
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                data = json.loads(r.read().decode())
        except Exception as e:  # timeout, refused, HTTP error
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
