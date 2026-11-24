"""Opponent model: short-term recent pieces + long-term frequency statistics (doc §9).

Only public history (pieces already submitted) is used. Predictions are labelled as guesses.
"""
from __future__ import annotations

import json
from collections import Counter, deque
from pathlib import Path

from .core import PIECES



class OpponentModel:
    def __init__(self, mode: str, profile_path: Path | None = None, recent_n: int = 12):
        self.mode = mode
        self.recent: deque[str] = deque(maxlen=recent_n)
        self.counts: Counter[str] = Counter()
        self.bag_pos: dict[str, list[int]] = {p: [] for p in PIECES}
        self._bag_index = 0
        self.max_streak = 1
        self._streak = 0
        self._last = ""
        self.profile_path = profile_path
        self.long_term = self._load()

    def _load(self) -> dict:
        if self.profile_path and self.profile_path.exists():
            try:
                return json.loads(self.profile_path.read_text())
            except (OSError, json.JSONDecodeError):
                pass
        return {}

    def observe(self, piece: str, pressure_delta: float | None = None) -> None:
        self.recent.append(piece)
        self.counts[piece] += 1
        self._streak = self._streak + 1 if piece == self._last else 1
        self._last = piece
        self.max_streak = max(self.max_streak, self._streak)
        if self.mode == "bag7":
            self.bag_pos[piece].append(self._bag_index)
            self._bag_index = (self._bag_index + 1) % 7

    def total(self) -> int:
        return sum(self.counts.values())

    def favored(self) -> list[str]:
        n = self.total()
        if n < 6 or self.mode == "bag7":
            return []
        return [p for p in PIECES if self.counts[p] >= 3 and self.counts[p] / n > 1.7 / 7]

    def held_back(self) -> str:
        """In 7-bag: the piece the player tends to keep until late in each bag."""
        best, best_avg = "", 0.0
        for p, pos in self.bag_pos.items():
            if len(pos) >= 2:
                avg = sum(pos) / len(pos)
                if avg > best_avg:
                    best, best_avg = p, avg
        return best if best_avg >= 4.5 else ""

    def hint(self) -> str:
        """Short token for the model prompt."""
        fav = self.favored()
        if fav:
            return "".join(fav)
        hb = self.held_back()
        return f"late{hb}" if hb else ""

    def tendency_text(self) -> str:
        fav = self.favored()
        if fav:
            return f"模型推测：玩家偏爱 {'/'.join(fav)}"
        hb = self.held_back()
        if hb:
            return f"模型推测：玩家习惯把 {hb} 留到每袋后段"
        if self._streak >= 3:
            return f"模型注意到：玩家连续出了 {self._streak} 个 {self._last}"
        if self.total() < 6:
            return "模型正在观察玩家的出题习惯…"
        return "暂未发现明显的出题偏好"

    def summary(self) -> dict:
        n = self.total()
        return {
            "recent": "".join(self.recent),
            "freq": {p: self.counts[p] for p in PIECES},
            "total": n,
            "favored": self.favored(),
            "tendency": self.tendency_text(),
            "hint": self.hint(),
            "long_term_games": self.long_term.get("games", 0),
        }

    def save_game(self, pieces_to_topout: int | None) -> None:
        if not self.profile_path:
            return
        lt = self.long_term
        lt["games"] = lt.get("games", 0) + 1
        freq = lt.setdefault("freq", {p: 0 for p in PIECES})
        for p in PIECES:
            freq[p] = freq.get(p, 0) + self.counts[p]
        if pieces_to_topout is not None:
            lt.setdefault("topout_lengths", []).append(pieces_to_topout)
            lt["topout_lengths"] = lt["topout_lengths"][-50:]
        try:
            self.profile_path.parent.mkdir(parents=True, exist_ok=True)
            self.profile_path.write_text(json.dumps(lt, indent=1))
        except OSError:
            pass
