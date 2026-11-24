"""Agent service: newline-delimited JSON over TCP (127.0.0.1:47800 by default).

    python3 -m agent.server [--port 47800] [--llm-url http://127.0.0.1:8080/v1]

Client -> server: hello | new_game | submit_piece | get_replay | ping
Server -> client: hello | state | thinking | decision | replay | error | pong
"""
from __future__ import annotations

import argparse
import json
import os
import socketserver
import sys
import traceback
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "agent"

from agent.llm import LLMClient  # noqa: E402
from agent.session import Session  # noqa: E402

DEFAULT_PORT = int(os.environ.get("TBT_PORT", "47800"))


class Handler(socketserver.StreamRequestHandler):
    def send(self, msg: dict) -> None:
        self.wfile.write((json.dumps(msg, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        self.wfile.flush()

    def handle(self) -> None:
        srv: Server = self.server  # type: ignore[assignment]
        session = Session(LLMClient(srv.llm_url, srv.llm_model, srv.llm_timeout))
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

    def __init__(self, port: int, llm_url: str, llm_model: str, llm_timeout: float):
        self.llm_url, self.llm_model, self.llm_timeout = llm_url, llm_model, llm_timeout
        super().__init__(("127.0.0.1", port), Handler)


def main() -> None:
    from agent.llm import DEFAULT_MODEL, DEFAULT_TIMEOUT, DEFAULT_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--llm-url", default=DEFAULT_URL)
    ap.add_argument("--llm-model", default=DEFAULT_MODEL)
    ap.add_argument("--llm-timeout", type=float, default=DEFAULT_TIMEOUT)
    a = ap.parse_args()
    llm = LLMClient(a.llm_url, a.llm_model, a.llm_timeout)
    print(f"[agent] Block the Bot agent service on 127.0.0.1:{a.port}", flush=True)
    print(f"[agent] LLM endpoint {a.llm_url} -> {'online' if llm.available() else 'offline (heuristic fallback)'}",
          flush=True)
    with Server(a.port, a.llm_url, a.llm_model, a.llm_timeout) as srv:
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
