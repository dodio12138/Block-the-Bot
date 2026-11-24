"""Calibrate challenge budgets: play each challenge with a one-ply adversarial attacker.

    python3 experiments/calibrate.py [--games 5]
"""
import argparse, random, statistics, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from agent.core import CoreGame
from agent.session import load_challenges, rows_from_strings


def play(ch, seed, cap=300):
    rng = random.Random(seed)
    g = CoreGame(mode=ch["mode"], hold=ch["hold"])
    if ch["board"]:
        g.set_rows(rows_from_strings(ch["board"]))
    while g.phase != 2 and g.state()["pieces_submitted"] < cap:
        pr = {p: v for p, v in g.pressure().items() if v is not None}
        worst = min(pr.values())
        g.submit(rng.choice([p for p, v in pr.items() if v == worst]))
        if g.phase == 2:
            break
        g.apply(g.best()[0])
    st = g.state()
    return st["pieces_submitted"], st["game_over"]


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--games", type=int, default=5); a = ap.parse_args()
    for ch in load_challenges():
        res = [play(ch, s) for s in range(a.games)]
        n = [r[0] for r in res]
        wins = sum(1 for r in res if r[1] and r[0] <= ch["budget"])
        print(f"{ch['id']:<11} budget={ch['budget']:<4} adversary median={statistics.median(n):<6} "
              f"min={min(n):<4} beats_budget={wins}/{a.games}")
