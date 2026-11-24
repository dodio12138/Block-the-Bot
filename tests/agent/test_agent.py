"""Agent service tests: core binding, protocol, policies (with a fake OpenAI-compatible LLM), session, server.

    python3 -m unittest discover -s tests/agent -v
"""
import json
import random
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.core import CoreGame, verify_replay  # noqa: E402
from agent.llm import LLMClient, parse_action_id, parse_strategy  # noqa: E402
from agent.protocol import encode_direct, select_top_k  # noqa: E402
from agent.session import Session, replay_frames  # noqa: E402


class FakeLLM(BaseHTTPRequestHandler):
    reply = "1"
    calls = 0

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
        self._send({"data": [{"id": "fake-qwen3-0.6b"}]})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        json.loads(self.rfile.read(n))
        type(self).calls += 1
        self._send({"choices": [{"message": {"content": type(self).reply}}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 2}})


def start_fake():
    srv = HTTPServer(("127.0.0.1", 0), FakeLLM)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"


class CoreBindingTest(unittest.TestCase):
    def test_flow_and_replay(self):
        g = CoreGame(mode="bag7", hold=False)
        for p in "TSZIOJL":
            self.assertEqual(g.submit(p), 0)
            cid, _ = g.best()
            self.assertGreaterEqual(g.apply(cid), 0)
        self.assertEqual(g.allowed(), "IOTSZJL")
        status, h, err = verify_replay(g.replay_text())
        self.assertEqual(status, 0, err)
        self.assertEqual(h, g.state()["hash"])

    def test_bag_rejects_repeat(self):
        g = CoreGame(mode="bag7")
        g.submit("T"); g.apply(g.best()[0])
        self.assertEqual(g.submit("T"), -2)


class ProtocolTest(unittest.TestCase):
    def test_topk_and_encoding(self):
        g = CoreGame(mode="free", hold=True)
        g.submit("T")
        cands, scores = g.candidates(), g.scores()
        ids = select_top_k(cands, scores, 8)
        self.assertEqual(len(ids), 8)
        self.assertEqual(len(set(ids)), 8)
        best = max(range(len(scores)), key=lambda i: scores[i])
        self.assertIn(cands[best]["id"], ids)
        p = encode_direct(g.state(), cands, scores, "L2", 8, "SZ", shuffle_seed=3)
        self.assertEqual(sorted(p.mapping), sorted(ids))
        self.assertIn("P=T", p.user)
        self.assertIn("OPP=SZ", p.user)
        self.assertEqual(p.user.count(";", p.user.index("C=")), 7)
        full = encode_direct(g.state(), cands, scores, "L1")
        self.assertEqual(len(full.mapping), len(cands))

    def test_parsers(self):
        self.assertEqual(parse_action_id("3"), 3)
        self.assertEqual(parse_action_id('<think>\nmaybe 5\n</think>\n{"action_id": 2}'), 2)
        self.assertIsNone(parse_action_id("no idea"))
        self.assertEqual(parse_strategy('ok {"strategy":"dig","valid_for":3}')["strategy"], "dig")
        self.assertIsNone(parse_strategy("{bad json"))


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "challenges.json").write_text((ROOT / "data" / "challenges.json").read_text())
        self.data = d

    def tearDown(self):
        self.tmp.cleanup()

    def play(self, session, pieces):
        msgs = []
        for p in pieces:
            msgs.extend(session.submit(p))
        return msgs

    def test_heuristic_game_and_replay_file(self):
        s = Session(LLMClient("http://127.0.0.1:9/v1", timeout_s=0.2), self.data)
        st = s.new_game({"mode": "free", "agent": "heuristic"})[0]
        self.assertEqual(st["type"], "state")
        self.assertEqual(set(st["pressure"]), set("IOTSZJL"))
        msgs = []
        rng = random.Random(1)
        for _ in range(400):
            pr = {k: v for k, v in s.state_msg()["pressure"].items() if v is not None}
            top = max(pr.values())
            msgs.extend(s.submit(rng.choice(sorted(k for k, v in pr.items() if v == top))))
            if s.winner:
                break
        self.assertEqual(s.winner, "human")
        dec = [m for m in msgs if m["type"] == "decision"]
        self.assertTrue(all(m["source"] == "heuristic" for m in dec))
        self.assertTrue(all(m["path"] for m in dec if not m["hold_only"]))
        rep = s.replay("last")
        self.assertEqual(rep["type"], "replay")
        self.assertTrue(rep["ok"], rep.get("error"))
        self.assertTrue((self.data / "leaderboard.json").exists())

    def test_direct_llm_valid_and_invalid(self):
        srv, url = start_fake()
        try:
            s = Session(LLMClient(url, timeout_s=2), self.data, write_files=False)
            s.new_game({"mode": "bag7", "agent": "llm", "top_k": 4})
            FakeLLM.reply = "2"
            msgs = self.play(s, "T")
            d = next(m for m in msgs if m["type"] == "decision")
            self.assertEqual(d["source"], "llm")
            self.assertTrue(d["llm_called"])
            self.assertFalse(d["fallback"])
            self.assertEqual(d["tokens_in"], 120)
            FakeLLM.reply = "99"  # not in the candidate list -> must fall back
            msgs = self.play(s, "S")
            d = next(m for m in msgs if m["type"] == "decision")
            self.assertEqual(d["source"], "fallback")
            self.assertTrue(d["fallback"])
        finally:
            srv.shutdown()

    def test_timeout_is_call_failure_not_invalid(self):
        srv, url = start_fake()
        try:
            s = Session(LLMClient(url, timeout_s=2), self.data, write_files=False)
            s.new_game({"mode": "bag7", "agent": "llm"})
            from agent.llm import LLMResult
            s.llm.chat = lambda *a, **k: LLMResult(False, latency_ms=6000.0, error="TimeoutError: timed out")
            d = next(m for m in self.play(s, "T") if m["type"] == "decision")
            self.assertEqual(d["source"], "fallback")
            self.assertEqual(s.stats["call_failures"], 1)
            self.assertEqual(s.stats["invalid_outputs"], 0)
            self.assertIsNone(s.state_msg()["dev"]["legal_rate"])
        finally:
            srv.shutdown()

    def test_offline_llm_falls_back(self):
        s = Session(LLMClient("http://127.0.0.1:9/v1", timeout_s=0.2), self.data, write_files=False)
        s.new_game({"mode": "bag7", "agent": "llm"})
        d = next(m for m in self.play(s, "I") if m["type"] == "decision")
        self.assertEqual(d["source"], "fallback")
        self.assertFalse(d["llm_called"])

    def test_hierarchical_sparse_calls(self):
        srv, url = start_fake()
        try:
            FakeLLM.reply = '{"strategy":"survival","weights":[8,5,3,2,1],"valid_for":5}'
            FakeLLM.calls = 0
            s = Session(LLMClient(url, timeout_s=2), self.data, write_files=False)
            s.new_game({"mode": "bag7", "agent": "hier"})
            self.play(s, "IOTSZJL" + "IOTSZJL")
            st = s.state_msg()
            self.assertEqual(st["dev"]["turns"], 14)
            self.assertLess(st["dev"]["llm_calls"], 14)
            self.assertGreaterEqual(st["dev"]["llm_calls"], 2)
            self.assertEqual(st["dev"]["policy"]["strategy"]["strategy"], "survival")
        finally:
            srv.shutdown()

    def test_challenge_budget(self):
        s = Session(LLMClient("http://127.0.0.1:9/v1", timeout_s=0.2), self.data, write_files=False)
        st = s.new_game({"mode": "challenge", "challenge_id": "bag_master"})[0]
        self.assertEqual(st["session"]["budget_left"], 130)
        self.assertGreater(len(s.challenges), 3)

    def test_stale_version_rejected(self):
        s = Session(LLMClient("http://127.0.0.1:9/v1", timeout_s=0.2), self.data, write_files=False)
        s.new_game({"mode": "free"})
        m = list(s.submit("T", state_version=12345))
        self.assertEqual(m[0]["type"], "error")


class TeacherDataTest(unittest.TestCase):
    def test_teacher_binding(self):
        g = CoreGame(mode="free", hold=False)
        g.submit("T")
        cid, vals = g.teacher(top_n=6)
        self.assertTrue(0 <= cid < g.candidate_count)
        self.assertEqual(len(vals), g.candidate_count)
        self.assertEqual(sum(v is not None for v in vals), 6)
        self.assertEqual(g.teacher(top_n=1)[0], g.best()[0])

    def test_search_policy_in_session(self):
        s = Session(LLMClient("http://127.0.0.1:9/v1", timeout_s=0.2), write_files=False)
        s.new_game({"mode": "bag7", "agent": "search"})
        d = next(m for m in s.submit("T") if m["type"] == "decision")
        self.assertEqual(d["source"], "search")

    def test_generator_labels_and_split(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            r = subprocess.run([sys.executable, str(ROOT / "training" / "gen_teacher_data.py"), "--games", "5",
                                "--max-pieces", "12", "--out", tmp, "--split", "0.6,0.2,0.2"],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stderr)
            games = {}
            for split in ("train", "val", "test"):
                for line in open(Path(tmp) / f"{split}.jsonl"):
                    smp = json.loads(line)
                    m = smp["meta"]
                    self.assertEqual(m["mapping"][m["label"]], m["teacher_id"])
                    self.assertEqual(smp["messages"][2]["content"], str(m["label"]))
                    self.assertIn(f"C=", smp["messages"][1]["content"])
                    games.setdefault(m["game"], set()).add(split)
            self.assertTrue(all(len(v) == 1 for v in games.values()))  # no game crosses splits
            ev = subprocess.run([sys.executable, str(ROOT / "training" / "eval_offline.py"), "--data",
                                 str(Path(tmp) / "train.jsonl"), "--no-model"], capture_output=True, text=True)
            self.assertEqual(ev.returncode, 0, ev.stderr)
            self.assertIn("L2/K=8", json.loads(ev.stdout)["results"])


class GameFeatureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        (d / "challenges.json").write_text((ROOT / "data" / "challenges.json").read_text())
        self.data = d

    def tearDown(self):
        self.tmp.cleanup()

    def test_speculation_hits_and_matches_live(self):
        import time as _t
        srv, url = start_fake()
        try:
            FakeLLM.reply = "1"
            s = Session(LLMClient(url, timeout_s=3), self.data, write_files=False)
            st = s.new_game({"mode": "bag7", "agent": "llm", "speculate": 7})[0]
            # each of the 7 pieces gets a prepared answer; wait for the background thread
            deadline = _t.time() + 10
            while _t.time() < deadline and not all(j.status == "done" for j in s.spec._jobs.values()):
                _t.sleep(0.05)
            self.assertEqual(len(s.spec._jobs), 7)
            ref = s.game.clone()
            ref.submit("T")
            d = next(m for m in s.submit("T", st["game"]["state_version"]) if m["type"] == "decision")
            self.assertTrue(d["speculative"])
            self.assertEqual(s.stats["spec_hits"], 1)
            # the prepared answer equals what the live path computes for the same state
            from agent.policies import Context
            live = s.policy.decide(Context(ref, ref.state(), ref.candidates(), "", ""))
            self.assertEqual(live.action_id, d["action_id"])
            self.assertIn("AI 把 T", d["friendly"])
        finally:
            srv.shutdown()

    def test_difficulty_mapping_and_fallback(self):
        s = Session(LLMClient("http://127.0.0.1:9/v1", timeout_s=0.2), self.data, write_files=False)
        st = s.new_game({"mode": "free", "difficulty": "easy"})[0]
        self.assertEqual(st["session"]["agent"], "casual")
        self.assertEqual(st["session"]["difficulty_label"], "简单")
        st = s.new_game({"mode": "free", "difficulty": "normal"})[0]
        self.assertEqual(st["session"]["agent"], "heuristic")  # model offline -> classic AI
        st = s.new_game({"mode": "free", "difficulty": "hard"})[0]
        self.assertEqual(st["session"]["agent"], "search")
        d = next(m for m in s.submit("I") if m["type"] == "decision")
        self.assertEqual(d["source"], "search")

    def test_stars_and_daily(self):
        from agent.session import daily_challenge, stars_for
        self.assertEqual([stars_for(n, 100) for n in (40, 50, 60, 75, 99)], [3, 3, 2, 2, 1])
        a, b = daily_challenge("20261124"), daily_challenge("20261124")
        self.assertEqual(a, b)
        self.assertNotEqual(a["board"], daily_challenge("20261125")["board"])
        self.assertGreaterEqual(a["budget"], 15)
        s = Session(LLMClient("http://127.0.0.1:9/v1", timeout_s=0.2), self.data, write_files=False)
        self.assertTrue(s.challenges[0]["id"].startswith("daily-"))
        h = s.hello()
        self.assertEqual([d["id"] for d in h["difficulties"]], ["easy", "normal", "hard"])
        self.assertIn("stars", h["challenges"][0])


class EmbeddedWorkerTest(unittest.TestCase):
    def test_grammars(self):
        from agent.worker_llm import id_grammar, strategy_grammar
        self.assertEqual(id_grammar(3), 'root ::= "0" | "1" | "2"')
        g = strategy_grammar(["dig", "flat"])
        self.assertIn('strat ::= "\\"dig\\"" | "\\"flat\\""', g)

    @unittest.skipUnless((ROOT / "game" / "bin" / "tbt_ai_worker").exists(), "tbt_ai_worker not built")
    def test_mock_worker_session(self):
        from agent.worker_llm import WorkerLLM
        w = WorkerLLM(backend="mock", timeout_s=5)
        try:
            self.assertTrue(w.available(force=True))
            self.assertEqual(w.status()["url"], "embedded")
            s = Session(w, write_files=False)
            s.new_game({"mode": "bag7", "agent": "llm"})
            d = next(m for m in s.submit("T") if m["type"] == "decision")
            self.assertEqual(d["source"], "llm")  # mock answers "0", always a legal local id
            s.new_game({"mode": "bag7", "agent": "hier"})
            d = next(m for m in s.submit("S") if m["type"] == "decision")
            self.assertEqual(d["strategy"]["source"], "llm")
        finally:
            w.close()

    @unittest.skipUnless((ROOT / "game" / "bin" / "tbt_ai_worker").exists()
                         and list((ROOT / "game" / "models").glob("*.gguf")), "worker or model missing")
    def test_real_model_constrained(self):
        from agent.worker_llm import WorkerLLM, id_grammar
        w = WorkerLLM(timeout_s=60)
        try:
            self.assertTrue(w.available(force=True), w.error)
            r = w.chat("Reply with a number.", "Pick one of 0, 1, 2. /no_think", max_tokens=4, grammar=id_grammar(3))
            self.assertTrue(r.ok, r.error)
            self.assertIn(r.text.strip(), {"0", "1", "2"})
        finally:
            w.close()


class FakeCloud(BaseHTTPRequestHandler):
    KEY = "sk-test-123"
    bodies: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _authed(self):
        return self.headers.get("Authorization") == f"Bearer {self.KEY}"

    def do_GET(self):
        if not self._authed():
            return self._send(401, {"error": "bad key"})
        self._send(200, {"data": [{"id": "cloud-flash"}, {"id": "cloud-pro"}]})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        type(self).bodies.append(body)
        if not self._authed():
            return self._send(401, {"error": "bad key"})
        if "THINK_FOREVER" in body["messages"][-1]["content"]:
            return self._send(200, {"choices": [{"message": {"content": "", "reasoning_content": "..."},
                                                 "finish_reason": "length"}]})
        text = '{"strategy":"dig","valid_for":4}' if body.get("response_format") else "3"
        self._send(200, {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 300, "completion_tokens": 1}})


class CloudProviderTest(unittest.TestCase):
    def setUp(self):
        import agent.providers as prov
        self.prov = prov
        self.tmp = tempfile.TemporaryDirectory()
        self._old = prov.CONFIG_PATH
        prov.CONFIG_PATH = Path(self.tmp.name) / "llm.json"
        self.srv = HTTPServer(("127.0.0.1", 0), FakeCloud)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}/v1"
        FakeCloud.bodies = []

    def tearDown(self):
        self.prov.CONFIG_PATH = self._old
        self.srv.shutdown()
        self.tmp.cleanup()

    def test_cloud_client_body_and_auth(self):
        c = LLMClient(self.url, "cloud-flash", 5, api_key=FakeCloud.KEY, kind="cloud", provider="custom")
        self.assertTrue(c.available(force=True))
        self.assertFalse(c.supports_grammar)
        r = c.chat("sys", "C=0:1;1:2\nOUT=ID /no_think", max_tokens=8, grammar="root ::= \"0\"")
        self.assertTrue(r.ok)
        b = FakeCloud.bodies[-1]
        self.assertNotIn("grammar", b)
        self.assertNotIn("chat_template_kwargs", b)
        self.assertNotIn("/no_think", b["messages"][1]["content"])
        c2 = LLMClient(self.url, "cloud-flash", 5, api_key=FakeCloud.KEY, kind="cloud",
                       extra_body={"thinking": {"type": "disabled"}})
        self.assertTrue(c2.chat("sys", "x", max_tokens=8).ok)
        self.assertEqual(FakeCloud.bodies[-1]["thinking"], {"type": "disabled"})
        from agent.providers import resolve
        self.assertEqual(resolve("deepseek", {})["extra_body"], {"thinking": {"type": "disabled"}})
        bad = LLMClient(self.url, "cloud-flash", 5, api_key="wrong", kind="cloud")
        self.assertFalse(bad.available(force=True))
        self.assertIn("401", bad.error)
        empty = c.chat("sys", "THINK_FOREVER", max_tokens=8)
        self.assertFalse(empty.ok)
        self.assertIn("思考", empty.error)
        missing = LLMClient(self.url, "nope", 5, api_key=FakeCloud.KEY, kind="cloud")
        self.assertFalse(missing.available(force=True))

    def test_session_with_cloud_policies(self):
        c = LLMClient(self.url, "cloud-flash", 5, api_key=FakeCloud.KEY, kind="cloud", provider="custom")
        s = Session(c, write_files=False)
        s.new_game({"mode": "bag7", "agent": "llm"})
        d = next(m for m in s.submit("T") if m["type"] == "decision")
        self.assertEqual(d["source"], "llm")
        s.new_game({"mode": "bag7", "agent": "hier"})
        d = next(m for m in s.submit("S") if m["type"] == "decision")
        self.assertEqual(d["strategy"]["strategy"], "dig")
        self.assertEqual(FakeCloud.bodies[-1]["response_format"], {"type": "json_object"})

    def test_server_set_llm_and_key_handling(self):
        from agent.server import Server
        srv = Server(0, "http://127.0.0.1:9/v1", "x", 2.0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            sock = socket.create_connection(srv.server_address, timeout=10)
            f = sock.makefile("rwb")

            def call(o):
                f.write((json.dumps(o) + "\n").encode()); f.flush()
                return json.loads(f.readline())
            r = call({"type": "set_llm", "provider": "custom", "base_url": self.url, "model": "cloud-flash",
                      "api_key": FakeCloud.KEY, "remember": False})
            self.assertEqual(r["type"], "llm_config")
            self.assertTrue(r["status"]["available"], r["status"])
            self.assertEqual(r["models"], ["cloud-flash", "cloud-pro"])
            raw = json.dumps(r)
            self.assertNotIn(FakeCloud.KEY, raw)
            saved = json.loads(self.prov.CONFIG_PATH.read_text())
            self.assertEqual(saved["provider"], "custom")
            self.assertNotIn("api_key", saved["providers"]["custom"])
            self.assertEqual(oct(self.prov.CONFIG_PATH.stat().st_mode & 0o777), "0o600")
            call({"type": "set_llm", "provider": "custom", "api_key": FakeCloud.KEY, "remember": True})
            self.assertEqual(json.loads(self.prov.CONFIG_PATH.read_text())["providers"]["custom"]["api_key"], FakeCloud.KEY)
            m = call({"type": "list_models", "provider": "custom"})
            self.assertEqual(m["models"], ["cloud-flash", "cloud-pro"])
            g = call({"type": "get_llm"})
            self.assertNotIn(FakeCloud.KEY, json.dumps(g))
            self.assertTrue(any(p["id"] == "deepseek" for p in g["providers"]))
            sock.close()
        finally:
            srv.shutdown()
            srv.server_close()


class ServerTest(unittest.TestCase):
    def test_tcp_roundtrip(self):
        from agent.server import Server
        srv = Server(0, "http://127.0.0.1:9/v1", "x", 0.2)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            sock = socket.create_connection(srv.server_address, timeout=5)
            f = sock.makefile("rwb")

            def send(o):
                f.write((json.dumps(o) + "\n").encode()); f.flush()

            def recv():
                return json.loads(f.readline())
            send({"type": "hello", "protocol_version": 1})
            self.assertEqual(recv()["type"], "hello")
            send({"type": "new_game", "mode": "free", "agent": "heuristic"})
            st = recv()
            send({"type": "submit_piece", "piece": "T", "state_version": st["game"]["state_version"]})
            types = [recv()["type"] for _ in range(3)]
            self.assertEqual(types, ["thinking", "decision", "state"])
            sock.close()
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
