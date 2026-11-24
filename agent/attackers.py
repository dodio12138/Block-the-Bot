"""Scripted attackers for headless experiments and data generation (never shown future pieces to the AI)."""
from __future__ import annotations

import random
from typing import Callable, Optional

from .core import CoreGame

ATTACKERS = ("random", "adversarial", "sz", "mixed")


def pick_piece(kind: str, game: CoreGame, rng: random.Random, pressure: Optional[dict] = None) -> str:
    allowed = game.allowed()
    if kind == "mixed":
        kind = rng.choice(["random", "adversarial", "adversarial", "sz"])
    if kind == "sz":
        sz = [p for p in allowed if p in "SZ"]
        if sz and rng.random() < 0.7:
            return rng.choice(sz)
        return rng.choice(allowed)
    if kind == "adversarial":
        pr = pressure if pressure is not None else game.pressure()
        scores = {p: v for p, v in pr.items() if v is not None and p in allowed}
        if scores:
            worst = min(scores.values())  # raw scores: lowest best-score = hardest for the defender
            return rng.choice(sorted(p for p, v in scores.items() if v == worst))
    return rng.choice(allowed)


def attacker_fn(kind: str, seed: int) -> Callable[[CoreGame], str]:
    rng = random.Random(seed)
    return lambda g: pick_piece(kind, g, rng)
