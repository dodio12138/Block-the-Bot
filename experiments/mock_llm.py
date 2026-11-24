"""Mock OpenAI-compatible endpoint for exercising the LLM pipeline without a real model.

    python3 experiments/mock_llm.py [--port 8080] [--latency-ms 40]

Direct prompts get a random candidate id (sometimes an invalid one, to exercise fallback);
strategy prompts get a strategy JSON. NOT a real model — use llama-server for Qwen3-0.6B.
"""
import argparse
import json
import random
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

rng = random.Random(0)
LAT = 0.04


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        self._send({"object": "list", "data": [{"id": "mock-qwen3-0.6b"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        user = body["messages"][-1]["content"]
        time.sleep(LAT)
        if "OUT=JSON" in user:
            name = rng.choice(["survival", "dig", "flat", "balanced", "clear"])
            text = json.dumps({"strategy": name, "weights": [rng.randint(2, 9) for _ in range(5)],
                               "valid_for": rng.randint(3, 6)})
        else:
            n = len(re.findall(r"[;=](\d+):", user.split("C=", 1)[-1])) or 1
            text = str(rng.randrange(n)) if rng.random() > 0.08 else "I think 42"
        self._send({"choices": [{"message": {"role": "assistant", "content": text}}],
                    "usage": {"prompt_tokens": len(user) // 3, "completion_tokens": len(text) // 3 + 1}})


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--latency-ms", type=float, default=40)
    a = ap.parse_args()
    LAT = a.latency_ms / 1000
    print(f"mock LLM on http://127.0.0.1:{a.port}/v1")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()
