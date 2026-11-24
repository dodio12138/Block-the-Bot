"""Offline evaluation of a model endpoint on teacher data (doc §10 step 1: "无训练基线").

    python3 training/eval_offline.py --data data/sft/v1/test.jsonl [--level L2] [--limit 300]
                                     [--llm-url http://127.0.0.1:8080/v1]

Reports, per level, against the teacher label:
  legal rate (parsable id inside the shown list), exact-match accuracy, latency P50/P95, tokens;
and model-free reference baselines on the same samples: heuristic-best, always-first, uniform random.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.llm import DEFAULT_MODEL, DEFAULT_URL, LLMClient, parse_action_id  # noqa: E402


def pct(xs, q):
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 1) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--level", default="")
    ap.add_argument("--limit", type=int, default=300)
    ap.add_argument("--llm-url", default=DEFAULT_URL)
    ap.add_argument("--llm-model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--no-model", action="store_true", help="only compute the model-free baselines")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    samples = [json.loads(l) for l in open(a.data, encoding="utf-8")]
    if a.level:
        samples = [s for s in samples if s["meta"]["level"] == a.level]
    samples = samples[: a.limit]
    llm = LLMClient(a.llm_url, a.llm_model, a.timeout)
    use_model = not a.no_model and llm.available(force=True)
    if not a.no_model and not use_model:
        print(f"[eval] endpoint {a.llm_url} offline -> baselines only", file=sys.stderr)

    agg = defaultdict(lambda: defaultdict(list))
    for i, s in enumerate(samples):
        m = s["meta"]
        key = f"L2/K={m['k']}" if m["level"] == "L2" else f"{m['level']}/all"
        r = agg[key]
        r["k"].append(m["k"])
        label, k = m["label"], m["k"]
        r["heuristic"].append(m["heuristic_local"] == label)
        r["first"].append(label == 0)
        r["random"].append(1.0 / k)
        if use_model:
            res = llm.chat(s["messages"][0]["content"], s["messages"][1]["content"], max_tokens=8)
            pred = parse_action_id(res.text) if res.ok else None
            legal = pred is not None and 0 <= pred < k
            r["legal"].append(legal)
            r["acc"].append(legal and pred == label)
            r["lat"].append(res.latency_ms)
            r["tok_in"].append(res.prompt_tokens)
            r["pos"].append(pred if legal else -1)
        if (i + 1) % 50 == 0:
            print(f"[eval] {i + 1}/{len(samples)}", file=sys.stderr)

    report = {}
    for key, r in sorted(agg.items()):
        row = {"n": len(r["heuristic"]), "avg_candidates": round(statistics.mean(r["k"]), 1),
               "baseline_heuristic_acc": round(statistics.mean(r["heuristic"]), 3),
               "baseline_first_acc": round(statistics.mean(r["first"]), 3),
               "baseline_random_acc": round(statistics.mean(r["random"]), 3)}
        if r["legal"]:
            pos = [p for p in r["pos"] if p >= 0]
            row.update({"model_legal_rate": round(statistics.mean(r["legal"]), 3),
                        "model_acc": round(statistics.mean(r["acc"]), 3),
                        "p50_ms": pct(r["lat"], 0.5), "p95_ms": pct(r["lat"], 0.95),
                        "avg_tok_in": round(statistics.mean(r["tok_in"]), 1),
                        "model_most_common_pos": max(set(pos), key=pos.count) if pos else None,
                        "model_most_common_pos_share": round(max(pos.count(p) for p in set(pos)) / len(pos), 3)
                        if pos else None})
        report[key] = row
    out = {"data": a.data, "endpoint": a.llm_url if use_model else None,
           "served_model": llm.served_model if use_model else None, "results": report}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    if a.out:
        Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
