"""The coordinator agent (SPEC § 11): plans rounds, spawns passes, re-runs, pings, halts.

Its tool surface is this Protocol and nothing else. No tool sets an exposure, a status or a constant:
the planner cannot move the bar.

There is no native tool calling: each turn is one `ask_json` call on the `coordinator` role that returns
`{"tool": <name in TOOL_NAMES>, "args": {...}}`. The caller's `CoordinatorTools` executes it and the result is
fed back as the next user message, at most `MAX_TURNS` turns a round, ending when `plan_round` has run.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from friday.domain.types import Arm
from friday.ports.models import ChatResult, Message, ModelClient
from friday.roles.ask import Rejected, ask_json
from friday.roles.judge import fill
from friday.roles.prompts import PromptBook

ROLE = "coordinator"
MAX_TURNS = 4          # tool turns per round (the S11 brief); not a run.yml figure yet (seam request)


class CoordinatorTools(Protocol):
    def plan_round(self) -> str:
        """Writes plans/round-NN.md from in-flight candidates, due ablations and last round's failures."""
        ...

    def spawn_pass(self, category: str, arm: Arm, worker: int) -> str: ...
    def rerun(self, pass_id: str) -> str:
        """Re-runs a pass that failed for transport, once."""
        ...

    def read_ledger(self, decision_id: str) -> Mapping[str, Any]: ...
    def read_journal(self) -> Mapping[str, Any]: ...
    def ping(self, reason: str) -> None: ...
    def halt(self, reason: str) -> None: ...


TOOL_NAMES = ("plan_round", "spawn_pass", "rerun", "read_ledger", "read_journal", "ping", "halt")

# tool -> (argument name -> converter): the only arguments a call may carry
TOOL_ARGS: dict[str, dict[str, Callable[[Any], Any]]] = {
    "plan_round": {},
    "spawn_pass": {"category": str, "arm": Arm, "worker": int},
    "rerun": {"pass_id": str},
    "read_ledger": {"decision_id": str},
    "read_journal": {},
    "ping": {"reason": str},
    "halt": {"reason": str},
}
_SPAWN_ARMS = frozenset({Arm.JOURNAL, Arm.NOTES})   # the empty arm is built only by the readout


@dataclass(frozen=True)
class ToolCall:
    tool: str
    args: Mapping[str, Any]


def parse_tool_call(raw: Any) -> ToolCall:
    """The tool call a decoded reply names; raises Rejected on an unknown tool or a wrong argument set."""
    if not isinstance(raw, dict):
        raise Rejected("reply is not a JSON object")
    tool, args = raw.get("tool"), raw.get("args") or {}
    if tool not in TOOL_NAMES:
        raise Rejected(f"tool {tool!r} is not one of {', '.join(TOOL_NAMES)}")
    if not isinstance(args, dict):
        raise Rejected("args is not a JSON object")
    want = TOOL_ARGS[tool]
    if set(args) != set(want):
        raise Rejected(f"{tool} takes {sorted(want) or 'no arguments'}, got {sorted(args)}")
    out: dict[str, Any] = {}
    for name, conv in want.items():
        if isinstance(args[name], (dict, list)):
            raise Rejected(f"{tool}.{name} is not a plain value")
        try:
            out[name] = conv(args[name])
        except (TypeError, ValueError) as e:
            raise Rejected(f"{tool}.{name}: {e}") from None
    if tool == "spawn_pass" and out["arm"] not in _SPAWN_ARMS:
        raise Rejected("spawn_pass arm is journal or notes")
    return ToolCall(tool, out)


@dataclass(frozen=True)
class Turn:
    call: ToolCall | None          # None: the reply stayed unusable after its re-asks
    result: str
    failures: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoundState:
    """What the coordinator reads at round start: ids and counts, no figure of the evaluation."""

    round: int
    rounds: int
    requests: tuple[str, ...]
    pairs: str
    in_flight: tuple[str, ...]
    failed: tuple[str, ...]


def render_messages(book: PromptBook, state: RoundState, max_turns: int) -> list[Message]:
    """The coordinator's opening messages for one round."""
    system = fill(book.text("coordinator/system.md").strip(), max_turns=max_turns)
    user = fill(book.text("coordinator/user.md").strip(), round=state.round, rounds=state.rounds,
                requests=", ".join(state.requests) or "none", pairs=state.pairs or "none",
                in_flight=", ".join(state.in_flight) or "none", failed=", ".join(state.failed) or "none")
    return [Message("system", system), Message("user", user)]


def _result_text(value: Any) -> str:
    if value is None:
        return "done"
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


@dataclass
class Coordinator:
    """Runs one round's coordinator turns against `CoordinatorTools`."""

    client: ModelClient
    book: PromptBook
    reasks: int                                   # run.yml json.reasks
    max_turns: int = MAX_TURNS
    results: list[ChatResult] = field(default_factory=list)

    def run_round(self, state: RoundState, tools: CoordinatorTools) -> list[Turn]:
        """The turns taken: at most `max_turns`, stopping after `plan_round` or an unusable reply.

        A tool that raises ValueError or LookupError (an unknown decision id) is reported back to the model as
        the turn's result; any other error propagates."""
        convo = render_messages(self.book, state, self.max_turns)
        turns: list[Turn] = []
        for n in range(self.max_turns):
            asked = ask_json(self.client, ROLE, convo, parse_tool_call, reasks=self.reasks,
                             tag=f"coordinator:r{state.round:02d}:{n + 1}")
            self.results.extend(asked.results)
            if asked.value is None:
                turns.append(Turn(None, "unusable reply", asked.failures))
                break
            call = asked.value
            try:
                result = _result_text(getattr(tools, call.tool)(**call.args))
            except (ValueError, LookupError) as e:
                result = f"error: {e}"
            turns.append(Turn(call, result, asked.failures))
            if call.tool == "plan_round":
                break
            convo = [*convo, Message("assistant", asked.results[-1].text),
                     Message("user", f"Result of {call.tool}: {result}")]
        return turns
