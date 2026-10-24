"""NOT SHIPPED experiment E2 (kept for reproducibility; not invoked by code/main.py).

Experiment E2: a tool-calling investigator for pairs still AMBIGUOUS/CONFLICT after attribution.

Unit of work: one batch (cases + unresolved resources that share deletion-worker rows). The model plans and
calls read-only tools (investigator_tools.py) for at most `max_tool_calls` turns, then must answer; every
proposal goes through investigator_verifier.py, which alone decides. Accepted pairs get INFERRED_AGENT.
Each model turn is cached by sha256(task + prompt_version + batch + transcript so far), so --from-cache replays
the whole loop with zero network calls.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from agent.attribution import AttributionResult, CaseContext
from agent.identity import IdentityUniverse
from e2_investigator_tools import ToolBox
from e2_investigator_verifier import Decision, verify
from agent.llm_client import AllProvidersFailed, LLMClient, ResponseCache, ValidationFailed, content_key
from agent.parsers import EvidenceIndex
from agent.schema import ActivityClaim, Attribution, Edge

TASK = "investigator"


def _validate_turn(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("action") not in ("tool", "final"):
        raise ValidationFailed('reply must be {"action": "tool", ...} or {"action": "final", "proposals": [...]}')
    if payload["action"] == "tool" and (not isinstance(payload.get("tool"), str) or not isinstance(payload.get("args"), dict)):
        raise ValidationFailed("a tool call needs a string 'tool' and an object 'args'")
    if payload["action"] == "final":
        props = payload.get("proposals")
        if not isinstance(props, list) or any(not isinstance(p, dict) or p.get("decision") not in ("ASSIGN", "AMBIGUOUS")
                                               for p in props):
            raise ValidationFailed("proposals must be a list of objects with decision ASSIGN or AMBIGUOUS")
    return payload


@dataclass
class BatchResult:
    batch_id: str
    pairs: list[list[str]]
    proposals: list[dict] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    tool_calls: int = 0
    model_calls: int = 0
    tokens: int = 0
    trace: list[dict] = field(default_factory=list)
    error: str = ""


def build_batches(result: AttributionResult, cases: dict[str, CaseContext], only_cases: set[str] | None = None) -> dict:
    """Connected components of unresolved (case, resource) pairs."""
    pairs = sorted((cid, a.resource_id) for cid, attrs in result.per_case.items() for a in attrs
                   if a.label in ("AMBIGUOUS", "CONFLICT") and a.in_scope)
    adj: dict[str, set[str]] = {}
    for cid, rid in pairs:
        adj.setdefault(f"c:{cid}", set()).add(f"r:{rid}")
        adj.setdefault(f"r:{rid}", set()).add(f"c:{cid}")
    seen, batches = set(), {}
    for node in sorted(adj):
        if node in seen:
            continue
        stack, comp = [node], set()
        while stack:
            n = stack.pop()
            if n in comp:
                continue
            comp.add(n)
            stack.extend(adj[n] - comp)
        seen |= comp
        cs = sorted(n[2:] for n in comp if n.startswith("c:"))
        if only_cases is not None and not set(cs) & only_cases:
            continue
        bid = f"batch_{hashlib.sha256('|'.join(sorted(comp)).encode()).hexdigest()[:10]}"
        batches[bid] = {"batch_id": bid, "pairs": [[c, r] for c, r in pairs if c in cs],
                        "cases": [{"case_id": c, "user_id": cases[c].subject.user_id,
                                   "reported_at": cases[c].request["reported_at"],
                                   "audit_at": cases[c].request["audit_at"]} for c in cs]}
    return batches


class Investigator:
    def __init__(self, client: LLMClient, cache: ResponseCache, index: EvidenceIndex, activity: list[ActivityClaim],
                 universe: IdentityUniverse, cases: dict[str, CaseContext]):
        self.client, self.cache, self.index, self.activity, self.universe, self.cases = \
            client, cache, index, activity, universe, cases
        self.base_prompt, self.prompt_version = client.prompt(TASK)
        self.max_calls = int(client.task_setting(TASK, "max_tool_calls", 8))

    def run_batch(self, batch: dict, all_batches: dict) -> BatchResult:
        tools = ToolBox(self.index, self.activity, self.universe, all_batches, self.cases)
        br = BatchResult(batch["batch_id"], batch["pairs"])
        transcript = [f"batch_id: {batch['batch_id']}\n{json.dumps(batch, sort_keys=True)}"]
        for turn in range(self.max_calls + 1):
            forced = turn == self.max_calls
            prompt = self.base_prompt + "\n" + "\n".join(transcript) + (
                '\n\nThe tool budget is spent. Answer now with {"action": "final", ...}.' if forced else "")
            key = content_key(TASK, self.prompt_version, prompt)
            hit = self.cache.get(TASK, key)
            if hit is not None:
                reply = hit["response"]
                self.client.ledger.cache_hits.append({"task": TASK, "key": key, "provider": hit.get("provider"),
                                                      "model": hit.get("model"), "call_id": hit.get("call_id")})
            else:
                try:
                    r = self.client.complete(TASK, prompt, _validate_turn)
                except AllProvidersFailed as exc:
                    br.error = str(exc)[:300]
                    break
                reply = r.data
                br.model_calls += 1
                br.tokens += int(r.usage.get("total_tokens", 0))
                self.cache.put(TASK, key, {"key": key, "task": TASK, "prompt_version": self.prompt_version,
                                           "batch_id": batch["batch_id"], "turn": turn, "response": reply,
                                           "provider": r.provider, "model": r.model, "key_slot": r.key_slot,
                                           "call_id": r.call_id, "batch_size": 1, "call_usage": r.usage})
            if reply["action"] == "final" or forced:
                br.proposals = reply.get("proposals", []) if reply["action"] == "final" else []
                break
            entry = tools.call(reply["tool"], reply["args"])
            br.tool_calls += 1
            transcript.append(f"tool call {entry['id']}: {reply['tool']}({json.dumps(reply['args'], sort_keys=True)})"
                              f"\nresult {entry['id']}: {json.dumps(entry['result'], sort_keys=True)[:6000]}")
        br.trace = tools.trace
        return br


def verify_batch(br: BatchResult, result: AttributionResult, owners: dict[str, list[str]],
                 account_resources: set[str], cases: dict[str, CaseContext]) -> list[Decision]:
    subject_users = {c: set(cases[c].subject.user_ids) for c in {p[0] for p in br.pairs}}
    with_account = {cid for cid in subject_users for a in result.per_case.get(cid, [])
                    if a.label in ("CONFIRMED", "INFERRED") and a.in_scope and a.system_id == "account_service"}
    br.decisions = verify(br.proposals, {"pairs": br.pairs}, br.trace, subject_users, owners, account_resources,
                          with_account)
    return br.decisions


def apply_decisions(result: AttributionResult, decisions: list[Decision], batch_id: str) -> int:
    n = 0
    for d in decisions:
        if not d.accepted:
            continue
        for cid, attrs in result.per_case.items():
            for i, a in enumerate(attrs):
                if a.resource_id != d.resource_id or a.label not in ("AMBIGUOUS", "CONFLICT"):
                    continue
                if cid == d.case_id:
                    attrs[i] = Attribution(cid, a.resource_id, a.system_id, "INFERRED_AGENT",
                                           f"investigator {batch_id}: {d.reason}", True, a.scope_reason,
                                           a.edges + [Edge(cid, a.resource_id, "AGENT", batch_id, "verified proposal",
                                                           "", "SECONDARY")])
                    n += 1
            result.per_case[cid] = [a for a in attrs if not (a.resource_id == d.resource_id and cid != d.case_id
                                                              and a.label in ("AMBIGUOUS", "CONFLICT"))]
    return n
