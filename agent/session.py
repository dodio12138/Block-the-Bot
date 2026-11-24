"""One game session: human attacker submits pieces, the defender policy answers.

Event flow per turn (doc §7): PieceSpawned -> (policy) -> PieceLocked / LinesCleared -> GameOver.
"""
from __future__ import annotations

import json
import secrets
import statistics
import time
from pathlib import Path
from typing import Iterator, Optional

from .core import PIECES, ROOT, CoreGame, core_version, verify_replay
from .llm import LLMClient
from .memory import OpponentModel
from .policies import Context, Decision, HierarchicalPolicy, Policy, make_policy

DATA = ROOT / "data"
PROTOCOL_VERSION = 1
BAG7_AI_TARGET = 150


def load_challenges(path: Path = DATA / "challenges.json") -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))["challenges"]
    except (OSError, KeyError, json.JSONDecodeError):
        return []


def rows_from_strings(lines: list[str]) -> list[int]:
    """Top-down strings ('X' filled, '.' empty) -> bottom-up row bitmasks."""
    rows = []
    for s in reversed(lines):
        v = 0
        for x, ch in enumerate(s):
            if ch not in ". ":
                v |= 1 << x
        rows.append(v)
    return rows


def normalize_pressure(scores: dict[str, Optional[float]]) -> dict[str, Optional[float]]:
    finite = [v for v in scores.values() if v is not None and v > -1e8]
    out: dict[str, Optional[float]] = {}
    if not finite:
        return {p: (1.0 if v is not None else None) for p, v in scores.items()}
    hi, lo = max(finite), min(finite)
    for p, v in scores.items():
        if v is None:
            out[p] = None
        elif v <= -1e8:
            out[p] = 1.0
        else:
            out[p] = round((hi - v) / (hi - lo), 3) if hi - lo > 1e-6 else 0.5
    return out


def replay_frames(text: str) -> dict:
    """Re-simulate a .tbr replay through the core and return per-step frames for playback."""
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    rules = next((l.split() for l in lines if l.startswith("rules ")), None)
    board = next((l.split()[1:] for l in lines if l.startswith("board")), [])
    if not rules:
        return {"ok": False, "error": "no rules line"}
    w, vis, hid, hold, topout, mode = (int(v) for v in rules[1:7])
    g = CoreGame(w, vis, hid, "free" if mode == 0 else "bag7", bool(hold), "any_above" if topout else "lock_out")
    g.set_rows([int(h, 16) for h in board])
    frames = [{"piece": "", "cells": [], "rows": g.state()["rows"], "lines": 0}]
    piece = ""
    for l in lines:
        parts = l.split()
        if parts[0] == "P":
            piece = parts[1]
            g.submit(piece)
            if g.phase == 2:
                frames.append({"piece": piece, "cells": [], "rows": g.state()["rows"], "lines": 0, "block_out": True})
        elif parts[0] == "A":
            cid = int(parts[1])
            c = g.candidates()[cid]
            lines_cleared = g.apply(cid)
            frames.append({"piece": c["piece"], "cells": c["cells"], "hold": c["hold"], "rows": g.state()["rows"],
                           "lines": lines_cleared})
    status, h, err = verify_replay(text)
    return {"ok": status == 0, "hash": h, "error": err, "frames": frames,
            "ruleset": {"width": w, "visible_height": vis, "hidden_rows": hid}}


class Session:
    def __init__(self, llm: LLMClient, data_dir: Path = DATA, write_files: bool = True):
        self.llm = llm
        self.data_dir = data_dir
        self.write_files = write_files
        self.challenges = load_challenges(data_dir / "challenges.json")
        self.game: Optional[CoreGame] = None
        self.policy: Optional[Policy] = None
        self.memory: Optional[OpponentModel] = None
        self.cfg: dict = {}
        self.winner: Optional[str] = None
        self.result_text = ""
        self.timeline: list[dict] = []
        self.stats: dict = {}
        self.game_id = ""
        self.replay_path = ""
        self.leaderboard_rank: Optional[int] = None

    # ------------------------------------------------------------------ setup
    def hello(self) -> dict:
        from .policies import POLICY_INFO
        self.llm.available(force=True)
        return {"type": "hello", "protocol_version": PROTOCOL_VERSION, "core_version": core_version(),
                "agents": POLICY_INFO, "challenges": [self._challenge_public(c) for c in self.challenges],
                "llm": self.llm.status()}

    @staticmethod
    def _challenge_public(c: dict) -> dict:
        return {k: c.get(k) for k in ("id", "name", "description", "mode", "hold", "budget")}

    def new_game(self, opts: dict) -> list[dict]:
        mode = opts.get("mode", "bag7")
        agent = opts.get("agent", "heuristic")
        challenge = None
        if mode == "challenge":
            challenge = next((c for c in self.challenges if c["id"] == opts.get("challenge_id")), None)
            if challenge is None:
                return [self._error("unknown challenge")]
            core_mode = challenge.get("mode", "free")
            hold = bool(challenge.get("hold", True))
        else:
            core_mode = "free" if mode == "free" else "bag7"
            hold = bool(opts.get("hold", core_mode == "free"))
        if self.game:
            self.game.close()
        self.game = CoreGame(mode=core_mode, hold=hold, topout=opts.get("topout", "lock_out"))
        if challenge and challenge.get("board"):
            st = self.game.set_rows(rows_from_strings(challenge["board"]))
            if st != 0:
                return [self._error(f"challenge board rejected ({st})")]
        self.policy = make_policy(agent, self.llm, opts)
        self.policy.reset()
        key = challenge["id"] if challenge else core_mode
        self.memory = OpponentModel(core_mode, self.data_dir / f"opponent_profile_{key}.json" if self.write_files else None)
        self.cfg = {
            "mode": mode, "core_mode": core_mode, "hold": hold, "agent": agent,
            "agent_label": getattr(self.policy, "label", agent),
            "challenge": self._challenge_public(challenge) if challenge else None,
            "budget": int(challenge["budget"]) if challenge else None,
            "ai_target": BAG7_AI_TARGET if (mode == "bag7") else None,
            "info_level": opts.get("info_level", "L2"), "top_k": int(opts.get("top_k", 8)),
            "board_key": key,
        }
        self.winner, self.result_text, self.timeline = None, "", []
        self.leaderboard_rank = None
        self.replay_path = ""
        self.stats = {"turns": 0, "llm_calls": 0, "fallbacks": 0, "invalid_outputs": 0, "call_failures": 0, "lat": [], "tok_in": [], "tok_out": [],
                      "decide_ms": [], "sources": {}}
        self.game_id = time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        self._log({"event": "GameStarted", "cfg": self.cfg, "core_version": core_version(),
                   "llm": self.llm.status()})
        return [self.state_msg()]

    # ------------------------------------------------------------------- turn
    def submit(self, piece: str, state_version: Optional[int] = None) -> Iterator[dict]:
        g = self.game
        if g is None:
            yield self._error("no game; send new_game first")
            return
        if self.winner:
            yield self._error("game finished")
            return
        before = g.state()
        if state_version is not None and int(state_version) != before["state_version"]:
            yield self._error("stale state_version", state=True)
            return
        piece = (piece or "").upper()
        st = g.submit(piece)
        if st != 0:
            msg = {-2: f"{piece} 不在本袋剩余方块中", -3: "未知方块"}.get(st, f"submit failed ({st})")
            yield self._error(msg, state=True)
            return
        self.memory.observe(piece)
        spawned = g.state()
        if spawned["game_over"]:  # block out: the piece cannot even spawn
            self._finish("human", f"AI 无法生成 {piece}（Block Out）——你赢了！")
            self._log({"event": "GameOver", "reason": "block_out", "piece": piece})
            yield self.state_msg()
            return
        yield {"type": "thinking", "event": "PieceSpawned", "piece": piece,
               "candidate_count": spawned["candidate_count"], "state_version": spawned["state_version"],
               "agent": self.cfg["agent"]}

        cands = g.candidates(paths=True)
        mem = self.memory.summary()
        ctx = Context(g, spawned, cands, mem["hint"], mem["recent"])
        t0 = time.perf_counter()
        try:
            d = self.policy.decide(ctx)
        except Exception as e:  # never let the agent crash the game
            cid, _ = g.best()
            d = Decision(cid, "fallback", f"policy error: {e}", fallback=True)
        decide_ms = (time.perf_counter() - t0) * 1000

        # Validate before execution (doc §8): phase, version, candidate id.
        now = g.state()
        if g.phase != 1 or now["state_version"] != spawned["state_version"] or not (0 <= d.action_id < len(cands)):
            cid, _ = g.best()
            d.events.append("ExecutionFailed")
            d.reason = f"动作 {d.action_id} 未通过验证 → 回退"
            d.action_id, d.source, d.fallback = cid, "fallback", True
            if isinstance(self.policy, HierarchicalPolicy):
                self.policy.notify_failure()

        chosen = cands[d.action_id]
        scores = g.scores()
        top = sorted(range(len(cands)), key=lambda i: -scores[i])[:3]
        lines = g.apply(d.action_id)
        after = g.state()
        risk_b, risk_a = before["features"]["risk"], after["features"]["risk"]
        delta = round(risk_a - risk_b, 3)
        events = ["PieceLocked"] + (["LinesCleared"] if lines > 0 else []) + d.events

        s = self.stats
        s["turns"] += 1
        s["sources"][d.source] = s["sources"].get(d.source, 0) + 1
        s["decide_ms"].append(decide_ms)
        if d.llm_called:
            s["llm_calls"] += 1
            s["lat"].append(d.inference_ms)
            s["tok_in"].append(d.tokens_in)
            s["tok_out"].append(d.tokens_out)
        if d.fallback:
            s["fallbacks"] += 1
            if d.llm_called and d.call_failed:
                s["call_failures"] += 1
            elif d.llm_called:
                s["invalid_outputs"] += 1

        n = after["pieces_submitted"]
        self.timeline.append({"n": n, "piece": piece, "placed": chosen["piece"], "lines": lines, "delta": delta,
                              "source": d.source, "hold": chosen["hold"], "hold_only": chosen["hold_only"],
                              "tspin": chosen["tspin"]})
        if after["game_over"]:
            self._finish("human", f"AI 在第 {n} 块 Top-out——你赢了！")
        elif self.cfg["budget"] and n >= self.cfg["budget"]:
            self._finish("ai", f"AI 撑过了全部 {self.cfg['budget']} 块预算——AI 获胜")
        elif self.cfg["ai_target"] and after["pieces_placed"] >= self.cfg["ai_target"]:
            self._finish("ai", f"AI 存活 {self.cfg['ai_target']} 块，达到目标——AI 获胜")

        self._log({"event": "Turn", "n": n, "piece": piece, "state_version": spawned["state_version"],
                   "candidates": len(cands), "decision": {k: v for k, v in d.to_dict().items() if k != "shown_ids"},
                   "shown": len(d.shown_ids), "lines": lines, "risk_before": risk_b, "risk_after": risk_a,
                   "decide_ms": round(decide_ms, 2), "events": events, "hash": after["hash"]})
        if self.winner:
            self._log({"event": "GameOver", "winner": self.winner, "pieces": n, "lines": after["lines_total"]})

        shown = [cands[i]["cells"] for i in d.shown_ids[:8]] if d.shown_ids else []
        yield {"type": "decision", "action_id": d.action_id, "source": d.source, "reason": d.reason,
               "piece": chosen["piece"], "cells": chosen["cells"], "path": chosen.get("path", []),
               "hold": chosen["hold"], "hold_only": chosen["hold_only"], "lines": lines,
               "tspin": chosen["tspin"], "risk_before": risk_b, "risk_after": risk_a, "pressure_delta": delta,
               "inference_ms": round(d.inference_ms, 1), "decide_ms": round(decide_ms, 1),
               "tokens_in": d.tokens_in, "tokens_out": d.tokens_out, "tokens_estimated": d.tokens_estimated,
               "llm_called": d.llm_called, "fallback": d.fallback, "prompt": d.prompt, "raw": d.raw,
               "strategy": d.strategy, "events": events, "candidate_count": len(cands),
               "top_cells": [cands[i]["cells"] for i in top], "shown_cells": shown}
        yield self.state_msg()

    # ---------------------------------------------------------------- helpers
    def state_msg(self) -> dict:
        g = self.game
        st = g.state()
        pressure = normalize_pressure(g.pressure()) if not self.winner and st["phase"] == "await_piece" else {}
        s = self.stats
        lat = sorted(s.get("lat", []))
        dev = {
            "turns": s.get("turns", 0), "llm_calls": s.get("llm_calls", 0),
            "calls_per_piece": round(s["llm_calls"] / s["turns"], 3) if s.get("turns") else 0.0,
            "fallback_rate": round(s["fallbacks"] / s["turns"], 3) if s.get("turns") else 0.0,
            # legal rate counts only answers that came back; call failures (timeouts) are reported separately
            "legal_rate": round(1 - s["invalid_outputs"] / (s["llm_calls"] - s["call_failures"]), 3)
            if s.get("llm_calls", 0) - s.get("call_failures", 0) > 0 else None,
            "call_fail_rate": round(s["call_failures"] / s["llm_calls"], 3) if s.get("llm_calls") else None,
            "p50_ms": round(statistics.median(lat), 1) if lat else None,
            "p95_ms": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1) if lat else None,
            "avg_tok_in": round(statistics.mean(s["tok_in"]), 1) if s.get("tok_in") else None,
            "avg_tok_out": round(statistics.mean(s["tok_out"]), 1) if s.get("tok_out") else None,
            "sources": s.get("sources", {}),
            "policy": self.policy.status() if self.policy else {},
        }
        budget_left = (self.cfg["budget"] - st["pieces_submitted"]) if self.cfg.get("budget") else None
        return {"type": "state", "game_id": self.game_id, "game": st, "session": {
            **self.cfg, "winner": self.winner, "result_text": self.result_text, "budget_left": budget_left,
            "replay_path": self.replay_path, "leaderboard_rank": self.leaderboard_rank,
            "llm": self.llm.status()},
            "pressure": pressure, "opponent": self.memory.summary() if self.memory else {},
            "timeline": self.timeline[-30:], "dev": dev}

    def _finish(self, winner: str, text: str) -> None:
        self.winner, self.result_text = winner, text
        st = self.game.state()
        if not self.write_files:
            return
        rep_dir = self.data_dir / "replays"
        rep_dir.mkdir(parents=True, exist_ok=True)
        path = rep_dir / f"{self.game_id}.tbr"
        path.write_text(f"# game {self.game_id} agent={self.cfg['agent']} mode={self.cfg['board_key']} "
                        f"winner={winner}\n" + self.game.replay_text())
        (rep_dir / f"{self.game_id}.json").write_text(json.dumps(
            {"game_id": self.game_id, "cfg": self.cfg, "winner": winner, "pieces": st["pieces_submitted"],
             "lines": st["lines_total"], "timeline": self.timeline, "hash": st["hash"]}, ensure_ascii=False, indent=1))
        self.replay_path = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        self.memory.save_game(st["pieces_submitted"] if winner == "human" else None)
        if winner == "human":
            self.leaderboard_rank = self._leaderboard(st["pieces_submitted"])

    def _leaderboard(self, pieces: int) -> Optional[int]:
        path = self.data_dir / "leaderboard.json"
        try:
            board = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            board = {}
        key = f"{self.cfg['board_key']}|{self.cfg['agent']}|hold={int(self.cfg['hold'])}"
        entries = board.setdefault(key, [])
        entries.append({"pieces": pieces, "game_id": self.game_id, "date": time.strftime("%Y-%m-%d %H:%M")})
        entries.sort(key=lambda e: e["pieces"])
        board[key] = entries[:10]
        path.write_text(json.dumps(board, ensure_ascii=False, indent=1))
        for i, e in enumerate(board[key]):
            if e["game_id"] == self.game_id:
                return i + 1
        return None

    def _log(self, rec: dict) -> None:
        if not self.write_files or not self.game_id:
            return
        d = self.data_dir / "logs"
        d.mkdir(parents=True, exist_ok=True)
        rec = {"t": round(time.time(), 3), **rec}
        with open(d / f"{self.game_id}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _error(self, message: str, state: bool = False) -> dict:
        msg = {"type": "error", "message": message}
        if state and self.game:
            msg["state"] = self.state_msg()
        return msg

    def replay(self, which: str = "last") -> dict:
        rep_dir = self.data_dir / "replays"
        path = None
        if which == "last":
            files = sorted(rep_dir.glob("*.tbr"))
            path = files[-1] if files else None
        else:
            cand = rep_dir / f"{Path(which).stem}.tbr"
            path = cand if cand.exists() else None
        if path is None:
            return self._error("没有可回放的对局")
        data = replay_frames(path.read_text())
        return {"type": "replay", "file": path.name, **data}
