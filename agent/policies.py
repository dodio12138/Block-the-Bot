"""Defender policies.

- heuristic : C++ baseline evaluator (also the fallback for every other policy)
- random    : uniform over legal candidates (sanity baseline)
- llm       : Direct Agent — the 0.6B model picks a candidate id every piece (doc §6.1)
- hier      : Hierarchical Agent — the model outputs a strategy vector every few pieces,
              a local controller picks each placement with argmax Q(s,a;weights) (doc §6.2)

Every decision records whether the model was actually called and whether a fallback was used,
so the model's contribution can be measured separately.
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass, field
from typing import Optional

from .core import DEFAULT_WEIGHTS, CoreGame
from .llm import LLMClient, parse_action_id, parse_strategy
from .protocol import encode_direct, encode_strategy
from .worker_llm import id_grammar, strategy_grammar

STRATEGY_PRESETS: dict[str, list[float]] = {
    # landing, lines, row_t, col_t, holes, wells, bump, max_h
    "balanced": DEFAULT_WEIGHTS,
    "survival": [-6.0, 4.0, -3.2, -9.3, -8.0, -3.4, -0.6, -2.5],
    "dig": [-4.0, 4.5, -3.5, -12.5, -14.0, -3.0, -0.3, 0.0],
    "flat": [-4.5, 3.4, -4.5, -9.3, -7.9, -5.5, -2.0, 0.0],
    "clear": [-3.5, 8.0, -3.0, -8.5, -7.0, -2.0, 0.0, 0.0],
}
STRATEGY_LABEL = {"balanced": "均衡", "survival": "求生（压低高度）", "dig": "挖洞", "flat": "平整表面",
                  "clear": "积极消行"}

# Which weight indices each strategy-vector group scales: [holes, height, bump, lines, wells]
_GROUPS = [[3, 4], [0, 7], [2, 6], [1], [5]]


def strategy_weights(strategy: dict) -> list[float]:
    name = strategy.get("strategy", "balanced")
    base = list(STRATEGY_PRESETS.get(name, DEFAULT_WEIGHTS))
    vec = strategy.get("weights")
    if isinstance(vec, list) and len(vec) == 5:
        for g, v in zip(_GROUPS, vec):
            try:
                m = min(max(float(v), 0.0), 10.0) / 5.0  # 5 = unchanged, clamp to [0, 2]x
            except (TypeError, ValueError):
                m = 1.0
            for i in g:
                base[i] *= max(m, 0.1)
    return base


@dataclass
class Decision:
    action_id: int
    source: str
    reason: str = ""
    inference_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_estimated: bool = False
    llm_called: bool = False
    fallback: bool = False
    call_failed: bool = False  # the model call itself failed (timeout / HTTP error), not a bad answer
    prompt: str = ""
    raw: str = ""
    strategy: Optional[dict] = None
    shown_ids: list[int] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Context:
    game: CoreGame
    state: dict
    candidates: list[dict]
    opp_hint: str
    recent: str


class Policy:
    name = "base"
    label = ""

    def decide(self, ctx: Context) -> Decision:
        raise NotImplementedError

    def status(self) -> dict:
        return {}

    def reset(self) -> None:
        pass


class HeuristicPolicy(Policy):
    name, label = "heuristic", "传统启发式"

    def decide(self, ctx: Context) -> Decision:
        cid, _ = ctx.game.best()
        return Decision(cid, "heuristic", "Dellacherie 风格一步评估")


class SearchPolicy(Policy):
    name, label = "search", "两步搜索（教师）"

    def __init__(self, top_n: int = 8, robust: float = 0.5):
        self.top_n, self.robust = top_n, robust

    def decide(self, ctx: Context) -> Decision:
        cid, _ = ctx.game.teacher(top_n=self.top_n, robust=self.robust)
        return Decision(cid, "search", f"对前 {self.top_n} 个落点 × 下一块所有可能做两步评估")


class RandomPolicy(Policy):
    name, label = "random", "随机（基线）"

    def __init__(self, seed: int = 0):
        self.rng = random.Random(seed)

    def decide(self, ctx: Context) -> Decision:
        return Decision(self.rng.randrange(len(ctx.candidates)), "random", "随机选择合法落点")


def _fallback(ctx: Context, why: str, d: Optional[Decision] = None) -> Decision:
    cid, _ = ctx.game.best()
    d = d or Decision(cid, "fallback")
    d.action_id, d.source, d.fallback = cid, "fallback", True
    d.reason = why
    return d


class DirectLLMPolicy(Policy):
    name, label = "llm", "模型直接决策"

    def __init__(self, llm: LLMClient, level: str = "L2", top_k: int = 8, shuffle: bool = True,
                 constrained: bool = True):
        self.llm, self.level, self.top_k, self.shuffle = llm, level, top_k, shuffle
        # constrained decoding: a GBNF grammar that only admits the shown candidate ids
        self.constrained = constrained and getattr(llm, "supports_grammar", False)

    def decide(self, ctx: Context) -> Decision:
        if not self.llm.available():
            return _fallback(ctx, "LLM 服务不可用 → 启发式回退")
        scores = ctx.game.scores()
        seed = ctx.state["state_version"] if self.shuffle else None
        prompt = encode_direct(ctx.state, ctx.candidates, scores, self.level, self.top_k, ctx.opp_hint, seed)
        grammar = id_grammar(len(prompt.mapping)) if self.constrained else None
        res = self.llm.chat(prompt.system, prompt.user, max_tokens=8, grammar=grammar)
        d = Decision(-1, "llm", llm_called=True, prompt=prompt.user, raw=res.text, inference_ms=res.latency_ms,
                     tokens_in=res.prompt_tokens, tokens_out=res.completion_tokens,
                     tokens_estimated=res.tokens_estimated, shown_ids=prompt.mapping)
        if not res.ok:
            d.call_failed = True
            return _fallback(ctx, f"模型调用失败（{res.error}）→ 回退", d)
        local = parse_action_id(res.text)
        if local is None or not (0 <= local < len(prompt.mapping)):
            return _fallback(ctx, f"模型输出不在候选集合中：{res.text!r} → 回退", d)
        d.action_id = prompt.mapping[local]
        d.reason = f"模型从 {len(prompt.mapping)} 个候选中选择了 #{local}"
        return d


class HierarchicalPolicy(Policy):
    name, label = "hier", "模型分层策略"

    def __init__(self, llm: LLMClient, valid_for: int = 5, risk_delta: float = 0.18):
        self.llm, self.default_valid, self.risk_delta = llm, valid_for, risk_delta
        self.reset()

    def reset(self) -> None:
        self.strategy: Optional[dict] = None
        self.weights = list(DEFAULT_WEIGHTS)
        self.remaining = 0
        self.plan_risk = 0.0
        self.plan_max = 0
        self.failed = False
        self.calls = 0

    def status(self) -> dict:
        return {"strategy": self.strategy, "remaining": self.remaining, "calls": self.calls}

    def _rule_strategy(self, f: dict) -> dict:
        if f["risk"] > 0.55:
            name = "survival"
        elif f["holes"] >= 3:
            name = "dig"
        elif f["bump"] > 12:
            name = "flat"
        else:
            name = "balanced"
        return {"strategy": name, "valid_for": self.default_valid, "source": "rule"}

    def _triggers(self, f: dict) -> list[str]:
        ev = []
        if self.strategy is None:
            ev.append("NoStrategy")
        elif self.remaining <= 0:
            ev.append("StrategyExpired")
        if self.strategy is not None and abs(f["risk"] - self.plan_risk) >= self.risk_delta:
            ev.append("BoardRiskChanged")
        replan_h = (self.strategy or {}).get("replan_max_height")
        if self.strategy is not None and replan_h and self.plan_max < replan_h <= f["max"]:
            ev.append("ReplanHeight")
        if self.failed:
            ev.append("ExecutionFailed")
        return ev

    def decide(self, ctx: Context) -> Decision:
        f = ctx.state["features"]
        events = self._triggers(f)
        d = Decision(-1, "hier-local", events=events)
        if events:
            self.failed = False
            strat = None
            if self.llm.available():
                prev = (self.strategy or {}).get("strategy", "")
                prompt = encode_strategy(ctx.state, ctx.opp_hint, ctx.recent, prev, ctx.state["lines_total"])
                grammar = strategy_grammar(list(STRATEGY_PRESETS)) if getattr(self.llm, "supports_grammar", False) else None
                kw = {"json_mode": True} if getattr(self.llm, "kind", "") == "cloud" else {}
                res = self.llm.chat(prompt.system, prompt.user, max_tokens=60, grammar=grammar, **kw)
                self.calls += 1
                d.llm_called, d.prompt, d.raw = True, prompt.user, res.text
                d.inference_ms, d.tokens_in, d.tokens_out = res.latency_ms, res.prompt_tokens, res.completion_tokens
                d.tokens_estimated = res.tokens_estimated
                d.call_failed = not res.ok
                obj = parse_strategy(res.text) if res.ok else None
                if obj and obj.get("strategy") in STRATEGY_PRESETS:
                    strat = {"strategy": obj["strategy"], "source": "llm",
                             "valid_for": int(min(max(int(obj.get("valid_for", self.default_valid) or 1), 1), 10))}
                    if isinstance(obj.get("weights"), list):
                        strat["weights"] = obj["weights"][:5]
                    if obj.get("replan_max_height") is not None:
                        try:
                            strat["replan_max_height"] = int(min(max(int(obj["replan_max_height"]), 4), 20))
                        except (TypeError, ValueError):
                            pass
                else:
                    d.fallback = True
                    d.reason = f"策略输出无效（{(res.error or res.text)[:60]!r}）→ 规则策略"
            else:
                d.fallback = True
                d.reason = "LLM 服务不可用 → 规则策略"
            if strat is None:
                strat = self._rule_strategy(f)
            self.strategy = strat
            self.weights = strategy_weights(strat)
            self.remaining = strat["valid_for"]
            self.plan_risk, self.plan_max = f["risk"], f["max"]
        cid, _ = ctx.game.best(self.weights)
        self.remaining -= 1
        d.action_id = cid
        d.strategy = dict(self.strategy or {})
        d.source = "hier-local"
        if not d.reason:
            name = (self.strategy or {}).get("strategy", "balanced")
            d.reason = f"本地控制器按「{STRATEGY_LABEL.get(name, name)}」策略选点（剩余 {self.remaining} 块）"
        return d

    def notify_failure(self) -> None:
        self.failed = True


def make_policy(name: str, llm: LLMClient, opts: dict) -> Policy:
    if name == "random":
        return RandomPolicy(int(opts.get("seed", 0)))
    if name == "llm":
        return DirectLLMPolicy(llm, opts.get("info_level", "L2"), int(opts.get("top_k", 8)),
                               constrained=bool(opts.get("constrained", True)))
    if name == "search":
        return SearchPolicy(int(opts.get("top_n", 8)), float(opts.get("robust", 0.5)))
    if name == "hier":
        return HierarchicalPolicy(llm, int(opts.get("valid_for", 5)))
    return HeuristicPolicy()


POLICY_INFO = [
    {"id": "heuristic", "label": HeuristicPolicy.label},
    {"id": "llm", "label": DirectLLMPolicy.label},
    {"id": "hier", "label": HierarchicalPolicy.label},
    {"id": "search", "label": SearchPolicy.label},
    {"id": "random", "label": RandomPolicy.label},
]
