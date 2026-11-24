"""Agent service: newline-delimited JSON over TCP (127.0.0.1:47800 by default).

    python3 -m agent.server [--port 47800] [--llm-url http://127.0.0.1:8080/v1]

Client -> server: hello | new_game | submit_piece | get_replay | ping | get_llm | set_llm | list_models
Server -> client: hello | state | thinking | decision | replay | error | pong | llm_config | models
"""
from __future__ import annotations

import argparse
import json
import os
import socketserver
import sys
import threading
import traceback
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "agent"

from agent.llm import LLMClient  # noqa: E402
from agent.providers import PRESETS, list_models, load_config, public_view, resolve, save_config  # noqa: E402
from agent.worker_llm import embedded_ready, shared_worker  # noqa: E402
from agent.session import Session  # noqa: E402

DEFAULT_PORT = int(os.environ.get("TBT_PORT", "47800"))


class Handler(socketserver.StreamRequestHandler):
    def send(self, msg: dict) -> None:
        self.wfile.write((json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        self.wfile.flush()

    def handle(self) -> None:
        srv: Server = self.server  # type: ignore[assignment]
        session = Session(srv.make_llm())
        print(f"[agent] client connected {self.client_address}", flush=True)
        for raw in self.rfile:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                t = msg.get("type")
                if t == "hello":
                    self.send(session.hello())
                elif t == "ping":
                    self.send({"type": "pong"})
                elif t == "new_game":
                    for out in session.new_game(msg):
                        self.send(out)
                elif t == "submit_piece":
                    for out in session.submit(msg.get("piece", ""), msg.get("state_version")):
                        self.send(out)
                elif t == "get_replay":
                    self.send(session.replay(msg.get("which", "last")))
                elif t == "get_llm":
                    self.send(srv.llm_config(session.llm))
                elif t == "set_llm":
                    session.llm = srv.set_llm(msg)  # takes effect from the next new_game
                    self.send(srv.llm_config(session.llm, models=True))
                elif t == "list_models":
                    self.send(srv.models_msg(msg))
                else:
                    self.send({"type": "error", "message": f"unknown message type {t!r}"})
            except (BrokenPipeError, ConnectionResetError):
                break
            except Exception as e:
                traceback.print_exc()
                try:
                    self.send({"type": "error", "message": f"server error: {e}"})
                except OSError:
                    break
        print(f"[agent] client disconnected {self.client_address}", flush=True)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, port: int, llm_url: str, llm_model: str, llm_timeout: float, backend: str = "http",
                 provider: str = ""):
        self.llm_url, self.llm_model, self.llm_timeout = llm_url, llm_model, llm_timeout
        self.provider = provider or ("embedded" if backend == "embedded" else "local")
        self.session_keys: dict[str, str] = {}  # API keys typed in the game, memory only
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", port), Handler)

    @property
    def backend(self) -> str:
        return "embedded" if self.provider == "embedded" else "http"

    def make_llm(self, provider: str = ""):
        provider = provider or self.provider
        if provider == "embedded":
            return shared_worker(self.llm_timeout)  # one loaded model shared by every session
        r = resolve(provider, load_config(), self.session_keys)
        if provider == "local" and not (load_config().get("providers") or {}).get("local"):
            r["base_url"], r["model"] = self.llm_url, self.llm_model  # CLI / env defaults
        timeout = self.llm_timeout if r["kind"] == "local" else max(self.llm_timeout, 20.0)
        return LLMClient(r["base_url"], r["model"], timeout, api_key=r["api_key"], kind=r["kind"],
                         provider=provider, label=r["label"])

    def set_llm(self, msg: dict):
        provider = str(msg.get("provider", ""))
        if provider not in PRESETS:
            provider = "custom"
        with self.lock:
            cfg = load_config()
            entry = cfg.setdefault("providers", {}).setdefault(provider, {})
            for k in ("model", "base_url"):
                if isinstance(msg.get(k), str) and msg[k].strip():
                    entry[k] = msg[k].strip()
            key = msg.get("api_key")
            if isinstance(key, str) and key.strip():
                self.session_keys[provider] = key.strip()
                if msg.get("remember"):
                    entry["api_key"] = key.strip()
            if msg.get("remember") is False:
                entry.pop("api_key", None)  # "don't remember" also forgets a previously saved key
            if msg.get("forget_key"):
                entry.pop("api_key", None)
                self.session_keys.pop(provider, None)
            cfg["provider"] = provider
            save_config(cfg)
            self.provider = provider
        llm = self.make_llm()
        llm.available(force=True)
        return llm

    def llm_config(self, llm, models: bool = False) -> dict:
        out = {"type": "llm_config", "current": self.provider, "providers": public_view(None, self.session_keys),
               "status": llm.status(), "embedded_ready": embedded_ready()}
        if models and self.provider not in ("embedded",):
            out["models"] = self.models_msg({"provider": self.provider})["models"]
        return out

    def models_msg(self, msg: dict) -> dict:
        provider = str(msg.get("provider", self.provider))
        if provider == "embedded":
            return {"type": "models", "provider": provider, "models": [], "error": ""}
        r = resolve(provider, load_config(), self.session_keys)
        key = msg.get("api_key") or r["api_key"]
        base = msg.get("base_url") or r["base_url"]
        if r["kind"] == "cloud" and not key:
            return {"type": "models", "provider": provider, "models": [], "error": "还没有填 API Key"}
        ids, err = list_models(base, key)
        return {"type": "models", "provider": provider, "models": ids, "error": err}


def main() -> None:
    from agent.llm import DEFAULT_MODEL, DEFAULT_TIMEOUT, DEFAULT_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--llm-url", default=DEFAULT_URL)
    ap.add_argument("--llm-model", default=DEFAULT_MODEL)
    ap.add_argument("--llm-timeout", type=float, default=DEFAULT_TIMEOUT)
    ap.add_argument("--llm", choices=["auto", "embedded", "http"], default=os.environ.get("TBT_LLM", "auto"),
                    help="auto: use the in-game worker + game/models if present, else the HTTP endpoint")
    a = ap.parse_args()
    explicit_http = "--llm-url" in sys.argv or bool(os.environ.get("TBT_LLM_URL"))
    saved = load_config().get("provider", "")
    if a.llm == "embedded":
        provider = "embedded"
    elif a.llm == "http" or explicit_http:
        provider = "local"
    elif saved in PRESETS and (saved != "embedded" or embedded_ready()):
        provider = saved  # what the player picked last time in the game's model settings
    else:
        provider = "embedded" if embedded_ready() else "local"
    backend = "embedded" if provider == "embedded" else "http"
    print(f"[agent] Block the Bot agent service on 127.0.0.1:{a.port}", flush=True)
    with Server(a.port, a.llm_url, a.llm_model, a.llm_timeout, backend, provider) as srv:
        llm = srv.make_llm()
        if backend == "embedded":
            ok = llm.available(force=True)  # load the model once at startup
            print(f"[agent] LLM: embedded worker -> {llm.served_model if ok else 'FAILED: ' + llm.error}", flush=True)
        else:
            ok = llm.available()
            print(f"[agent] LLM: {provider} {llm.base_url} {llm.model} -> "
                  f"{'online' if ok else 'offline (heuristic fallback): ' + llm.error}", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
