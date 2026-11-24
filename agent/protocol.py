"""Compact model-facing protocol (doc §5.3 / §5.4).

L0: bitboard + all candidates with full features
L1: summary features + all candidates
L2: summary features + diverse Top-K candidates

Candidate ids shown to the model are local (0..K-1); `mapping[local] = core id`.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

SYSTEM_DIRECT = (
    "You are the defender in a Tetris duel. A human chooses your pieces and wants you to top out. "
    "Pick ONE placement id from C. Fields per candidate: x=leftmost column, rot=rotation, hold=1 uses hold, "
    "clear=lines cleared, holes=holes after, max=max column height after, bump=surface bumpiness after. "
    "Prefer: clear lines, few holes, low max height, low bump. Reply with the id number only."
)

SYSTEM_STRATEGY = (
    "You are the strategy brain of a Tetris defender facing a human attacker. Choose how the local "
    "controller should weigh the board for the next few pieces. Strategies: survival (keep the stack low), "
    "dig (remove holes), flat (keep the surface even), clear (clear lines aggressively), balanced. "
    'Reply with JSON only, e.g. {"strategy":"survival","weights":[8,5,3,2,1],"valid_for":5}. '
    "weights = importance 0-10 of [holes, height, bump, lines, wells]; valid_for = pieces 1-10."
)


@dataclass
class Prompt:
    system: str
    user: str
    mapping: list[int] = field(default_factory=list)  # local id -> core candidate id
    level: str = "L2"


def _x(c: dict) -> int:
    cells = c.get("cells") or []
    return min(p[0] for p in cells) if cells else -1


def select_top_k(cands: list[dict], scores: list[float], k: int) -> list[int]:
    """Half best-by-heuristic, half diverse (distinct rot/x-bucket/hold); returns core ids."""
    order = sorted(range(len(cands)), key=lambda i: -scores[i])
    if k <= 0 or k >= len(cands):
        return [cands[i]["id"] for i in order]
    chosen: list[int] = []
    for i in order[: max(1, k // 2)]:
        chosen.append(i)
    seen = {(cands[i]["rot"], _x(cands[i]) // 3, cands[i]["hold"]) for i in chosen}
    for i in order:
        if len(chosen) >= k:
            break
        key = (cands[i]["rot"], _x(cands[i]) // 3, cands[i]["hold"])
        if i not in chosen and key not in seen:
            chosen.append(i)
            seen.add(key)
    for i in order:
        if len(chosen) >= k:
            break
        if i not in chosen:
            chosen.append(i)
    return [cands[i]["id"] for i in chosen]


def _header(state: dict, opp_hint: str) -> list[str]:
    f = state["features"]
    head = (f"V=1;P={state.get('current') or '-'};HOLD={state.get('hold') or '-'};"
            f"H={','.join(str(h) for h in f['heights'])};HO={f['holes']};MX={f['max']};RISK={f['risk']:.2f}")
    lines = [head]
    if opp_hint:
        lines.append(f"OPP={opp_hint}")
    return lines


def encode_direct(state: dict, cands: list[dict], scores: list[float], level: str = "L2", top_k: int = 8,
                  opp_hint: str = "", shuffle_seed: int | None = None, ids: list[int] | None = None) -> Prompt:
    """`ids` (already ordered) overrides candidate selection and shuffling."""
    by_id = {c["id"]: c for c in cands}
    if ids is None:
        if level == "L2":
            ids = select_top_k(cands, scores, top_k)
        else:
            ids = [c["id"] for c in cands]
        if shuffle_seed is not None:
            random.Random(shuffle_seed).shuffle(ids)
    ids = list(ids)
    lines = _header(state, opp_hint)
    if level == "L0":
        vis = state["ruleset"]["visible_height"]
        rows = state["rows"][: max(1, state["features"]["max"])][:vis]
        lines.append("B=" + ",".join(format(r, "x") for r in rows))
        lines.append("#id:x,rot,hold,clear,holes,max,bump,wells,land")
    else:
        lines.append("#id:x,rot,hold,clear,holes,max,bump")
    parts = []
    for local, cid in enumerate(ids):
        c = by_id[cid]
        f = c["f"]
        vals = [_x(c), c["rot"], 1 if c["hold"] else 0, c["lines"], f["holes"], f["max"], f["bump"]]
        if level == "L0":
            vals += [f["wells"], round(f["landing"], 1)]
        parts.append(f"{local}:" + ",".join(str(v) for v in vals))
    lines.append("C=" + ";".join(parts))
    lines.append("OUT=ID /no_think")
    return Prompt(SYSTEM_DIRECT, "\n".join(lines), ids, level)


def encode_strategy(state: dict, opp_hint: str, recent: str, prev: str, lines_total: int) -> Prompt:
    f = state["features"]
    user = (f"H={','.join(str(h) for h in f['heights'])};MX={f['max']};HO={f['holes']};BUMP={f['bump']};"
            f"WELL={f['max_well']};RISK={f['risk']:.2f};LINES={lines_total};PIECES={state['pieces_submitted']}\n"
            f"OPP={opp_hint or '-'};RECENT={recent or '-'};PREV={prev or '-'}\nOUT=JSON /no_think")
    return Prompt(SYSTEM_STRATEGY, user, [], "S")
