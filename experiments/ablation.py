"""Ablation / benchmark harness (doc §11): policies x attackers x information levels.

    python3 experiments/ablation.py --games 6 --max-pieces 150 \
        --configs heuristic,search,llm:L2:8,llm:L2:4,llm:L1,hier:5,hier:3 --attackers adversarial,random

Each config runs through the same Session code path as the game (validation, fallback, stats).
Reports survival, lines, top-out rate, model calls per piece, fallback and legal-output rates,
latency and tokens. Results are written to experiments/results/<timestamp>.json.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.attackers import pick_piece  # noqa: E402
from agent.llm import DEFAULT_MODEL, DEFAULT_URL, LLMClient  # noqa: E402
from agent.session import Session  # noqa: E402


def parse_config(c: str) -> dict:
    parts = c.split(":")
    opts = {"agent": parts[0]}
    if parts[0] == "llm":
        opts["info_level"] = parts[1] if len(parts) > 1 else "L2"
        opts["top_k"] = int(parts[2]) if len(parts) > 2 else 8
    elif parts[0] == "hier":
        opts["valid_for"] = int(parts[1]) if len(parts) > 1 else 5
    return opts


def run_game(session: Session, opts: dict, mode: str, hold: bool, attacker: str, seed: int, max_pieces: int) -> dict:
    rng = random.Random(seed)
    session.new_game({**opts, "mode": mode, "hold": hold, "seed": seed})
    g = session.game
    while not session.winner and g.state()["pieces_submitted"] < max_pieces:
        piece = pick_piece(attacker, g, rng)
        for _ in session.submit(piece):
            pass
    st = g.state()
    dev = session.state_msg()["dev"]
    return {"survived": st["pieces_placed"], "lines": st["lines_total"], "topout": st["game_over"],
            "llm_calls": dev["llm_calls"], "turns": dev["turns"], "fallbacks": session.stats["fallbacks"],
            "invalid": session.stats["invalid_outputs"], "call_failures": session.stats["call_failures"], "lat": list(session.stats["lat"]),
            "tok_in": list(session.stats["tok_in"]), "decide_ms": list(session.stats["decide_ms"])}


def summarize(games: list[dict]) -> dict:
    turns = sum(x["turns"] for x in games)
    calls = sum(x["llm_calls"] for x in games)
    failed = sum(x["call_failures"] for x in games)
    lat = sorted(v for x in games for v in x["lat"])
    tok = [v for x in games for v in x["tok_in"]]
    dms = [v for x in games for v in x["decide_ms"]]
    surv = [x["survived"] for x in games]
    return {
        "games": len(games), "mean_survived": round(statistics.mean(surv), 1),
        "median_survived": statistics.median(surv), "mean_lines": round(statistics.mean(x["lines"] for x in games), 1),
        "topout_rate": round(sum(x["topout"] for x in games) / len(games), 2),
        "calls_per_piece": round(calls / turns, 3) if turns else 0,
        "fallback_rate": round(sum(x["fallbacks"] for x in games) / turns, 3) if turns else 0,
        "legal_rate": round(1 - sum(x["invalid"] for x in games) / (calls - failed), 3) if calls - failed > 0 else None,
        "call_fail_rate": round(failed / calls, 3) if calls else None,
        "p50_ms": round(lat[len(lat) // 2], 1) if lat else None,
        "p95_ms": round(lat[min(len(lat) - 1, int(0.95 * len(lat)))], 1) if lat else None,
        "avg_tok_in": round(statistics.mean(tok), 1) if tok else None,
        "decide_ms_mean": round(statistics.mean(dms), 2) if dms else None,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="heuristic,search,llm:L2:8,hier:5")
    ap.add_argument("--attackers", default="adversarial,random")
    ap.add_argument("--mode", default="bag7")
    ap.add_argument("--hold", type=int, default=0)
    ap.add_argument("--games", type=int, default=6)
    ap.add_argument("--max-pieces", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--llm-url", default=DEFAULT_URL)
    ap.add_argument("--llm-model", default=DEFAULT_MODEL)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    llm = LLMClient(a.llm_url, a.llm_model)
    online = llm.available(force=True)
    print(f"LLM endpoint {a.llm_url}: {'online ' + llm.served_model if online else 'OFFLINE (llm/hier rows = fallback)'}",
          file=sys.stderr)
    session = Session(llm, write_files=False)
    rows = []
    for cfg in a.configs.split(","):
        opts = parse_config(cfg)
        for att in a.attackers.split(","):
            t0 = time.time()
            games = [run_game(session, opts, a.mode, bool(a.hold), att, a.seed * 1000 + i, a.max_pieces)
                     for i in range(a.games)]
            row = {"config": cfg, "attacker": att, **summarize(games), "seconds": round(time.time() - t0, 1)}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), file=sys.stderr, flush=True)

    cols = ["config", "attacker", "mean_survived", "median_survived", "mean_lines", "topout_rate", "calls_per_piece",
            "fallback_rate", "legal_rate", "call_fail_rate", "p50_ms", "p95_ms", "avg_tok_in", "decide_ms_mean"]
    print("| " + " | ".join(cols) + " |")
    print("|" + "---|" * len(cols))
    for r in rows:
        print("| " + " | ".join("-" if r[c] is None else str(r[c]) for c in cols) + " |")
    out = Path(a.out) if a.out else Path(__file__).resolve().parent / "results" / f"ablation_{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"args": vars(a), "llm_online": online, "served_model": llm.served_model, "rows": rows},
                              ensure_ascii=False, indent=1))
    print(f"\nsaved {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
