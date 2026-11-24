"""Speculative look-ahead: while the human is choosing, pre-compute the model's answer for the pieces they are
most likely to submit (highest pressure first). Uses only public information — the AI still does not know which
piece will come; it just prepared several answers. Only for stateless policies (direct decision).
"""
from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .core import CoreGame
from .memory import OpponentModel
from .policies import Context, Decision, Policy


@dataclass
class _Job:
    piece: str
    base_version: int
    game: CoreGame
    state: dict
    cands: list
    hint: str
    recent: str
    status: str = "pending"  # pending | running | done | cancelled
    decision: Optional[Decision] = None
    ms: float = 0.0
    done: threading.Event = field(default_factory=threading.Event)


class Speculator:
    def __init__(self) -> None:
        self._jobs: dict[str, _Job] = {}
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.started = 0
        self.hits = 0

    def start(self, game: CoreGame, memory: OpponentModel, policy: Policy, pieces: list[str]) -> None:
        """Prepare clones on the caller's thread (the core is not thread-safe), then decide in the background."""
        self.cancel()
        base = game.state()["state_version"]
        jobs: dict[str, _Job] = {}
        for p in pieces:
            c = game.clone()
            if c.submit(p) != 0 or c.phase != 1:
                c.close()
                continue
            mem = copy.deepcopy(memory)
            mem.profile_path = None
            mem.observe(p)
            summ = mem.summary()
            jobs[p] = _Job(p, base, c, c.state(), c.candidates(), summ["hint"], summ["recent"])
        if not jobs:
            return
        cancel = threading.Event()
        with self._lock:
            self._jobs, self._cancel = jobs, cancel
        self.started += len(jobs)
        order = list(jobs.values())

        def run() -> None:
            for j in order:
                if cancel.is_set():
                    break
                with self._lock:
                    if j.status != "pending":
                        continue
                    j.status = "running"
                t0 = time.perf_counter()
                try:
                    j.decision = policy.decide(Context(j.game, j.state, j.cands, j.hint, j.recent))
                except Exception:
                    j.decision = None
                j.ms = (time.perf_counter() - t0) * 1000
                j.status = "done"
                j.done.set()
            for j in order:  # anything not reached is cancelled; wake waiters
                if j.status == "pending":
                    j.status = "cancelled"
                    j.done.set()

        threading.Thread(target=run, daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def take(self, piece: str, base_version: int, spawned_hash: str, timeout: float) -> Optional[Decision]:
        """Answer prepared for `piece` from the same pre-submit state, waiting if it is still being computed."""
        with self._lock:
            job = self._jobs.get(piece)
            if job is None or job.base_version != base_version:
                self._cancel.set()
                return None
            if job.status == "pending":
                job.status = "cancelled"  # not started yet: the live path will compute it
                self._cancel.set()
                return None
        self._cancel.set()  # stop preparing other pieces; the one we need is running or done
        if not job.done.wait(timeout) or job.decision is None:
            return None
        if job.state.get("hash") != spawned_hash:
            return None
        self.hits += 1
        d = copy.copy(job.decision)
        d.speculative = True
        d.events = list(d.events) + ["SpeculativeHit"]
        return d
