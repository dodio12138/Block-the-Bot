"""ctypes binding for the C++ rules core (core/build/libtbt_core.*).

The C++ core is the single source of truth for rules, candidate generation and
the baseline evaluator. Python never edits the board directly.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Optional

PIECES = "IOTSZJL"
ROOT = Path(__file__).resolve().parent.parent

# Weight order matches tbt::Weights: landing, lines, row_t, col_t, holes, wells, bump, max_h
DEFAULT_WEIGHTS = [-4.500158825082766, 3.4181268101392694, -3.2178882868487753, -9.348695305445199,
                   -7.899265427351652, -3.3855972247263626, 0.0, 0.0]

STATUS = {0: "ok", -1: "wrong_phase", -2: "piece_not_allowed", -3: "bad_piece", -4: "bad_candidate",
          -5: "game_over", -6: "bad_board", -7: "parse_error", -8: "replay_mismatch"}


def _lib_path() -> Path:
    env = os.environ.get("TBT_CORE_LIB")
    if env:
        return Path(env)
    name = {"darwin": "libtbt_core.dylib", "win32": "tbt_core.dll"}.get(sys.platform, "libtbt_core.so")
    return ROOT / "core" / "build" / name


_lib = None


def lib():
    global _lib
    if _lib is not None:
        return _lib
    path = _lib_path()
    if not path.exists():
        raise RuntimeError(f"core library not found at {path}; build it with scripts/build_core.sh")
    L = ctypes.CDLL(str(path))
    vp, ci, cd = ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_double)
    L.tbt_version.restype = ctypes.c_char_p
    L.tbt_game_create.argtypes = [ci, ci, ci, ci, ci, ci]
    L.tbt_game_create.restype = vp
    L.tbt_game_destroy.argtypes = [vp]
    L.tbt_game_clone.argtypes = [vp]
    L.tbt_game_clone.restype = vp
    L.tbt_game_reset.argtypes = [vp]
    L.tbt_game_set_rows.argtypes = [vp, ctypes.POINTER(ctypes.c_uint), ci]
    L.tbt_game_set_rows.restype = ci
    for fn in ("tbt_game_submit", "tbt_game_apply"):
        getattr(L, fn).argtypes = [vp, ci]
        getattr(L, fn).restype = ci
    for fn in ("tbt_game_phase", "tbt_game_candidate_count", "tbt_game_allowed_mask"):
        getattr(L, fn).argtypes = [vp]
        getattr(L, fn).restype = ci
    L.tbt_game_best.argtypes = [vp, cd, ci, cd]
    L.tbt_game_best.restype = ci
    L.tbt_game_scores.argtypes = [vp, cd, ci, cd, ci]
    L.tbt_game_scores.restype = ci
    L.tbt_game_teacher.argtypes = [vp, cd, ci, ci, ctypes.c_double, cd, ci]
    L.tbt_game_teacher.restype = ci
    L.tbt_game_piece_pressure.argtypes = [vp, cd, ci, cd]
    L.tbt_game_piece_pressure.restype = ci
    L.tbt_game_state_json.argtypes = [vp]
    L.tbt_game_state_json.restype = ctypes.c_char_p
    L.tbt_game_candidates_json.argtypes = [vp, ci]
    L.tbt_game_candidates_json.restype = ctypes.c_char_p
    L.tbt_game_replay_text.argtypes = [vp]
    L.tbt_game_replay_text.restype = ctypes.c_char_p
    L.tbt_replay_verify.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ci]
    L.tbt_replay_verify.restype = ci
    _lib = L
    return L


def core_version() -> str:
    return lib().tbt_version().decode()


def _weights(w: Optional[list[float]]):
    w = list(w if w is not None else DEFAULT_WEIGHTS)
    return (ctypes.c_double * len(w))(*w), len(w)


class CoreGame:
    def __init__(self, width=10, visible=20, hidden=4, mode="bag7", hold=True, topout="lock_out"):
        self.mode = mode
        self.hold = hold
        self._h = lib().tbt_game_create(width, visible, hidden, 0 if mode == "free" else 1, 1 if hold else 0,
                                        1 if topout == "any_above" else 0)
        if not self._h:
            raise ValueError("invalid ruleset")

    def clone(self) -> "CoreGame":
        c = CoreGame.__new__(CoreGame)
        c.mode, c.hold = self.mode, self.hold
        c._h = lib().tbt_game_clone(self._h)
        return c

    def close(self):
        if self._h:
            lib().tbt_game_destroy(self._h)
            self._h = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def set_rows(self, rows_bottom_up: list[int]) -> int:
        arr = (ctypes.c_uint * max(1, len(rows_bottom_up)))(*rows_bottom_up)
        return lib().tbt_game_set_rows(self._h, arr, len(rows_bottom_up))

    def submit(self, piece: str) -> int:
        idx = PIECES.find(piece.upper()) if piece else -1
        if idx < 0:
            return -3
        return lib().tbt_game_submit(self._h, idx)

    def apply(self, candidate_id: int) -> int:
        return lib().tbt_game_apply(self._h, int(candidate_id))

    @property
    def phase(self) -> int:
        return lib().tbt_game_phase(self._h)

    @property
    def candidate_count(self) -> int:
        return lib().tbt_game_candidate_count(self._h)

    def allowed(self) -> str:
        m = lib().tbt_game_allowed_mask(self._h)
        return "".join(p for i, p in enumerate(PIECES) if m & (1 << i))

    def state(self) -> dict:
        return json.loads(lib().tbt_game_state_json(self._h))

    def candidates(self, paths: bool = False) -> list[dict]:
        return json.loads(lib().tbt_game_candidates_json(self._h, 1 if paths else 0))

    def best(self, weights=None) -> tuple[int, float]:
        w, n = _weights(weights)
        score = ctypes.c_double(0.0)
        cid = lib().tbt_game_best(self._h, w, n, ctypes.byref(score))
        return cid, score.value

    def scores(self, weights=None) -> list[float]:
        w, n = _weights(weights)
        cap = max(1, self.candidate_count)
        out = (ctypes.c_double * cap)()
        k = lib().tbt_game_scores(self._h, w, n, out, cap)
        return [out[i] for i in range(k)]

    def teacher(self, weights=None, top_n: int = 8, robust: float = 0.5) -> tuple[int, list[Optional[float]]]:
        """Two-ply search teacher. Returns (candidate id, per-candidate values or None if unexplored)."""
        w, n = _weights(weights)
        cap = max(1, self.candidate_count)
        out = (ctypes.c_double * cap)()
        cid = lib().tbt_game_teacher(self._h, w, n, top_n, robust, out, cap)
        vals = [None if out[i] != out[i] else out[i] for i in range(self.candidate_count)]
        return cid, vals

    def pressure(self, weights=None) -> dict[str, Optional[float]]:
        w, n = _weights(weights)
        out = (ctypes.c_double * 7)()
        lib().tbt_game_piece_pressure(self._h, w, n, out)
        res = {}
        for i, p in enumerate(PIECES):
            v = out[i]
            res[p] = None if v != v else v  # NaN -> not allowed
        return res

    def replay_text(self) -> str:
        return lib().tbt_game_replay_text(self._h).decode()


def verify_replay(text: str) -> tuple[int, str, str]:
    h = ctypes.create_string_buffer(17)
    err = ctypes.create_string_buffer(256)
    s = lib().tbt_replay_verify(text.encode(), h, err, 256)
    return s, h.value.decode(), err.value.decode()
