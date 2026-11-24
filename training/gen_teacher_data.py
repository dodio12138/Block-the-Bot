"""Generate SFT data: (state, candidates) -> teacher action, in chat-JSONL form (doc §10 steps 2-3).

    python3 training/gen_teacher_data.py --games 60 --out data/sft/v1

- Rollouts: scripted attackers (random / adversarial / S-Z heavy / mixed) vs a defender that follows
  the two-ply teacher, with epsilon-exploration among the heuristic top-8 so the data also covers
  states reached after imperfect moves.
- Label: the teacher's action (uses only public information: next piece unknown).
- Candidate order is shuffled per sample to suppress position bias; L2 samples force-include the
  teacher action if Top-K selection dropped it (rate reported in the manifest).
- Split by game: train / val / test never share a game.

Output: <out>/{train,val,test}.jsonl + manifest.json. Each line:
  {"messages":[system,user,assistant], "meta":{...}}  — loadable by common SFT trainers.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.attackers import ATTACKERS, pick_piece  # noqa: E402
from agent.core import CoreGame  # noqa: E402
from agent.memory import OpponentModel  # noqa: E402
from agent.protocol import encode_direct, select_top_k  # noqa: E402


def build_sample(state, cands, h_scores, teacher_id, heuristic_id, level, k, opp_hint, rng):
    forced = False
    if level == "L2":
        ids = select_top_k(cands, h_scores, k)
        if teacher_id not in ids:
            ids[-1] = teacher_id
            forced = True
    else:
        ids = [c["id"] for c in cands]
    rng.shuffle(ids)
    p = encode_direct(state, cands, h_scores, level, k, opp_hint, ids=ids)
    label = ids.index(teacher_id)
    return {
        "messages": [{"role": "system", "content": p.system}, {"role": "user", "content": p.user},
                     {"role": "assistant", "content": str(label)}],
        "meta": {"level": level, "k": len(ids), "label": label, "teacher_id": teacher_id,
                 "heuristic_local": ids.index(heuristic_id) if heuristic_id in ids else None,
                 "forced": forced, "mapping": ids},
    }


def play_game(gi: int, args, rng: random.Random) -> tuple[list[dict], dict]:
    mode = rng.choice(args.modes)
    hold = rng.random() < args.hold_prob
    attacker = args.attackers[gi % len(args.attackers)]
    g = CoreGame(mode=mode, hold=hold)
    if args.garbage_prob and rng.random() < args.garbage_prob:
        rows = []
        for _ in range(rng.randint(2, 10)):
            v = (1 << 10) - 1
            for h in rng.sample(range(10), rng.choice([1, 1, 2])):
                v &= ~(1 << h)
            rows.append(v)
        g.set_rows(rows)
    mem = OpponentModel(mode)
    samples, agree, turns = [], 0, 0
    while g.phase != 2 and g.state()["pieces_submitted"] < args.max_pieces:
        piece = pick_piece(attacker, g, rng)
        g.submit(piece)
        mem.observe(piece)
        if g.phase == 2:
            break
        state = g.state()
        cands = g.candidates()
        h_scores = g.scores()
        heuristic_id = max(range(len(cands)), key=lambda i: h_scores[i])
        teacher_id, _ = g.teacher(top_n=args.top_n, robust=args.robust)
        agree += teacher_id == heuristic_id
        turns += 1
        for level in args.levels:
            for k in (args.top_k if level == "L2" else [0]):
                s = build_sample(state, cands, h_scores, teacher_id, heuristic_id, level, k, mem.hint(), rng)
                s["meta"].update({"game": gi, "turn": turns, "mode": mode, "hold": hold, "attacker": attacker})
                samples.append(s)
        act = teacher_id
        if rng.random() < args.epsilon:
            act = rng.choice(sorted(range(len(cands)), key=lambda i: -h_scores[i])[:8])
        g.apply(act)
    st = g.state()
    return samples, {"game": gi, "mode": mode, "hold": hold, "attacker": attacker, "turns": turns,
                     "topout": st["game_over"], "lines": st["lines_total"], "teacher_agrees_heuristic": agree}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=60)
    ap.add_argument("--max-pieces", type=int, default=150)
    ap.add_argument("--out", default="data/sft/v1")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--modes", default="bag7,free")
    ap.add_argument("--attackers", default="random,adversarial,sz,mixed")
    ap.add_argument("--levels", default="L2,L1")
    ap.add_argument("--top-k", default="8")
    ap.add_argument("--hold-prob", type=float, default=0.5)
    ap.add_argument("--garbage-prob", type=float, default=0.3)
    ap.add_argument("--epsilon", type=float, default=0.1)
    ap.add_argument("--top-n", type=int, default=8)
    ap.add_argument("--robust", type=float, default=0.5)
    ap.add_argument("--split", default="0.8,0.1,0.1")
    args = ap.parse_args()
    args.modes = args.modes.split(",")
    args.attackers = [a for a in args.attackers.split(",") if a in ATTACKERS]
    args.levels = args.levels.split(",")
    args.top_k = [int(k) for k in args.top_k.split(",")]
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    fr = [float(x) for x in args.split.split(",")]
    n_train = int(round(args.games * fr[0]))
    n_val = int(round(args.games * fr[1]))
    files = {name: open(out / f"{name}.jsonl", "w", encoding="utf-8") for name in ("train", "val", "test")}
    counts, games_meta = Counter(), []
    labels, forced, agree, turns = Counter(), 0, 0, 0
    t0 = time.time()
    for gi in range(args.games):
        rng = random.Random(args.seed * 100003 + gi)
        samples, meta = play_game(gi, args, rng)
        split = "train" if gi < n_train else "val" if gi < n_train + n_val else "test"
        meta["split"] = split
        games_meta.append(meta)
        for s in samples:
            files[split].write(json.dumps(s, ensure_ascii=False) + "\n")
            counts[split] += 1
            labels[(s["meta"]["level"], s["meta"]["label"])] += 1
            forced += s["meta"]["forced"]
        agree += meta["teacher_agrees_heuristic"]
        turns += meta["turns"]
        print(f"game {gi + 1}/{args.games} [{split}] {meta['mode']} hold={int(meta['hold'])} "
              f"{meta['attacker']}: {meta['turns']} turns, {len(samples)} samples", flush=True)
    for f in files.values():
        f.close()
    l2 = sum(v for (lvl, _), v in labels.items() if lvl == "L2")
    manifest = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"), "seconds": round(time.time() - t0, 1),
        "args": {k: v for k, v in vars(args).items()}, "samples": dict(counts), "turns": turns,
        "teacher_agrees_heuristic": round(agree / max(1, turns), 3),
        "l2_forced_rate": round(forced / max(1, l2), 3),
        "l2_label_histogram": {str(i): labels[("L2", i)] for i in range(max(args.top_k))},
        "games": games_meta,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    print(json.dumps({k: manifest[k] for k in ("samples", "turns", "teacher_agrees_heuristic", "l2_forced_rate",
                                                "l2_label_histogram", "seconds")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
