"""Embedded inference: the game ships its own llama.cpp worker (game/bin/tbt_ai_worker) and model files
(game/models/*.gguf). The agent service starts the worker as a child process — no llama-server needed.

Same interface as llm.LLMClient (available / status / chat), plus optional GBNF-constrained decoding.
Latency is measured here; the worker reports token counts.
"""
from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from .core import ROOT
from .llm import LLMResult, estimate_tokens

GAME_BIN = ROOT / "game" / "bin"
GAME_MODELS = ROOT / "game" / "models"


def worker_path() -> Path:
    name = "tbt_ai_worker.exe" if sys.platform == "win32" else "tbt_ai_worker"
    return Path(os.environ.get("TBT_WORKER", GAME_BIN / name))


def find_models(models_dir: Path = GAME_MODELS) -> tuple[Optional[Path], Optional[Path]]:
    """(base model, LoRA adapter) found in game/models; env TBT_MODEL / TBT_LORA override.
    TBT_LORA=none disables the adapter."""
    base = Path(os.environ["TBT_MODEL"]) if os.environ.get("TBT_MODEL") else None
    lora = Path(os.environ["TBT_LORA"]) if os.environ.get("TBT_LORA") and os.environ["TBT_LORA"] != "none" else None
    if models_dir.is_dir():
        ggufs = sorted(models_dir.glob("*.gguf"))
        if base is None:
            bases = [p for p in ggufs if "lora" not in p.name.lower()]
            bases.sort(key=lambda p: (0 if "q8" in p.name.lower() else 1, p.name))
            base = bases[0] if bases else None
        if lora is None and os.environ.get("TBT_LORA") != "none":
            loras = sorted(p for p in ggufs if "lora" in p.name.lower())
            lora = loras[-1] if loras else None  # highest version name, e.g. bot-lora-v2 over v1
    return base, lora


def embedded_ready() -> bool:
    base, _ = find_models()
    return worker_path().exists() and base is not None and base.exists()


def id_grammar(k: int) -> str:
    """GBNF that only allows one of the candidate ids 0..k-1."""
    alts = " | ".join(f'"{i}"' for i in range(max(1, k)))
    return f"root ::= {alts}"


def strategy_grammar(names: list[str]) -> str:
    strat = " | ".join(f'"\\"{n}\\""' for n in names)
    return ('root ::= "{\\"strategy\\":" strat ",\\"valid_for\\":" [1-9] "}"\n'
            f"strat ::= {strat}")


class WorkerLLM:
    supports_grammar = True

    def __init__(self, worker: Optional[Path] = None, model: Optional[Path] = None, lora: Optional[Path] = None,
                 threads: int = 0, timeout_s: float = 12.0, ctx: int = 2048, backend: str = "llama"):
        self.worker = worker or worker_path()
        m, l = find_models()
        self.model_path = model or m
        self.lora_path = lora if lora is not None else l
        self.threads = threads or max(2, min(8, (os.cpu_count() or 8) // 2))
        self.timeout_s, self.ctx, self.backend = timeout_s, ctx, backend
        self.base_url = "embedded"
        self.model = self.model_path.name if self.model_path else ""
        self.served_model = ""
        self.error = ""
        self._proc: Optional[subprocess.Popen] = None
        self._q: "queue.Queue[dict]" = queue.Queue()
        self._lock = threading.Lock()
        self._next_id = 0
        self._last_attempt = 0.0

    # ------------------------------------------------------------ lifecycle
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and bool(self.served_model)

    def _start(self) -> bool:
        self._last_attempt = time.monotonic()
        if not self.worker.exists():
            self.error = f"找不到推理进程 {self.worker}（cmake --build core/build --target tbt_ai_worker）"
            return False
        args = [str(self.worker), f"--backend={self.backend}", f"--threads={self.threads}", f"--ctx={self.ctx}"]
        if self.backend == "llama":
            if not self.model_path or not self.model_path.exists():
                self.error = f"game/models 里没有模型文件（{self.model_path}）"
                return False
            args.append(f"--model={self.model_path}")
            if self.lora_path and self.lora_path.exists():
                args.append(f"--lora={self.lora_path}")
        self._q = queue.Queue()
        self._proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True, encoding="utf-8", bufsize=1)
        proc, q = self._proc, self._q

        def reader():
            for line in proc.stdout:  # type: ignore[union-attr]
                try:
                    q.put(json.loads(line))
                except json.JSONDecodeError:
                    continue
            q.put({"type": "exit"})

        def errlog():
            for line in proc.stderr:  # type: ignore[union-attr]
                print("[worker] " + line.rstrip(), file=sys.stderr, flush=True)

        threading.Thread(target=reader, daemon=True).start()
        threading.Thread(target=errlog, daemon=True).start()
        try:
            hello = q.get(timeout=120)
        except queue.Empty:
            hello = {"ok": False, "error": "模型加载超时"}
        if hello.get("type") != "hello" or not hello.get("ok"):
            self.error = str(hello.get("error", "推理进程启动失败"))
            self.close()
            return False
        self.served_model = str(hello.get("model", self.model))
        self.error = ""
        print(f"[agent] embedded inference ready: {self.served_model} (threads={self.threads})", flush=True)
        return True

    def available(self, force: bool = False) -> bool:
        with self._lock:
            if self._alive():
                return True
            if not force and time.monotonic() - self._last_attempt < 30.0 and self._last_attempt > 0:
                return False
            return self._start()

    def status(self) -> dict:
        return {"url": "embedded", "backend": "embedded", "model": self.model, "served_model": self.served_model,
                "available": self._alive(), "error": self.error}

    def close(self) -> None:
        p, self._proc = self._proc, None
        self.served_model = ""
        if p and p.poll() is None:
            try:
                p.stdin.write('{"type":"shutdown"}\n')  # type: ignore[union-attr]
                p.stdin.flush()  # type: ignore[union-attr]
                p.wait(timeout=2)
            except Exception:
                p.kill()

    # ---------------------------------------------------------------- calls
    def chat(self, system: str, user: str, max_tokens: int = 8, grammar: Optional[str] = None) -> LLMResult:
        if not self.available():
            return LLMResult(False, error=self.error or "embedded worker unavailable")
        with self._lock:
            self._next_id += 1
            rid = self._next_id
            req = {"type": "generate", "id": rid, "max_tokens": max_tokens, "temperature": 0,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
            if grammar:
                req["grammar"] = grammar
            t0 = time.perf_counter()
            try:
                self._proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")  # type: ignore[union-attr]
                self._proc.stdin.flush()  # type: ignore[union-attr]
            except (BrokenPipeError, OSError) as e:
                self.close()
                return LLMResult(False, error=f"worker pipe: {e}")
            deadline = t0 + self.timeout_s
            while True:
                left = deadline - time.perf_counter()
                if left <= 0:
                    try:
                        self._proc.stdin.write(json.dumps({"type": "cancel", "id": rid}) + "\n")  # type: ignore[union-attr]
                        self._proc.stdin.flush()  # type: ignore[union-attr]
                    except OSError:
                        pass
                    return LLMResult(False, latency_ms=(time.perf_counter() - t0) * 1000, error="TimeoutError: worker")
                try:
                    msg = self._q.get(timeout=left)
                except queue.Empty:
                    continue
                if msg.get("type") == "exit":
                    self.close()
                    return LLMResult(False, error="worker exited")
                if msg.get("id") != rid or msg.get("type") in ("token", "pong"):
                    continue  # stale reply of a cancelled request, or a streaming piece
                ms = (time.perf_counter() - t0) * 1000
                if msg.get("type") == "error":
                    return LLMResult(False, latency_ms=ms, error=str(msg.get("error")))
                text = str(msg.get("text", ""))
                pt = msg.get("prompt_tokens")
                ct = msg.get("tokens")
                est = pt is None or ct is None
                return LLMResult(True, text, int(pt) if pt is not None else estimate_tokens(system + user),
                                 int(ct) if ct is not None else estimate_tokens(text), est, ms)


_shared: Optional[WorkerLLM] = None


def shared_worker(timeout_s: float = 12.0) -> WorkerLLM:
    """One worker (one loaded model) for the whole agent service."""
    global _shared
    if _shared is None:
        _shared = WorkerLLM(timeout_s=timeout_s)
    return _shared
