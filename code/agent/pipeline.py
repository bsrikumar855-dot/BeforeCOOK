"""End-to-end run: load -> evidence index -> intent -> attribution -> state -> rules -> steps -> validate -> write.

One bad case never stops the run: each case is processed inside its own try/except and
degrades to INCOMPLETE,[] (DESIGN.md D10), which is counted and traced.
"""
from __future__ import annotations

import csv
import json
import traceback
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .attribution import AttributionResult, CaseContext, attribute, classify_request_rows, resource_owners
from .identity import IdentityUniverse
from .intent import MAX_CLASSIFY_CHARS, IntentClassifier, IntentResult, decide, model_text
from .llm_client import LLMClient, ResponseCache, UsageLedger
from .loaders import Dataset, load_dataset, parse_ts
from .parsers import EvidenceIndex, build_evidence_index, catalog_aliases
from .parsers.file_checks import DEGRADE_STATUSES
from .parsers.ocr_grounding import OcrReader
from .parsers.png_vlm import ImageExtractor
from .rules import DATA_FLOW, SYSTEM_CATEGORIES, Policy
from .state import reconstruct
from .steps import build_steps, serialize
from .validation import HEADER, EvidenceCatalog, case_sort_key, validate_row

WORKER = "deletion_worker"


@dataclass
class CaseOutcome:
    case_id: str
    status: str
    steps: list[dict]
    trace: dict
    degraded: str | None = None


@dataclass
class RunResult:
    dataset: Dataset
    index: EvidenceIndex
    outcomes: dict[str, CaseOutcome]
    attribution: AttributionResult
    ledger: UsageLedger
    summary: dict = field(default_factory=dict)


def run(dataset_dir: Path, from_cache: bool, include_samples: bool = True,
        image_extraction: bool = True, reextract: set[str] | None = None) -> RunResult:
    ds = load_dataset(dataset_dir, include_samples=include_samples)
    universe = IdentityUniverse(ds.users, ds.audit_log)
    ledger = UsageLedger()
    client = LLMClient(from_cache=from_cache, ledger=ledger)
    cache = ResponseCache()
    policy = Policy(ds.rules)
    categories = sorted(set(policy.action) - {DATA_FLOW}) + [DATA_FLOW]
    extractor = ImageExtractor(client, cache, universe, categories) if image_extraction else None
    if extractor is not None:
        extractor.aliases = catalog_aliases(ds, include_user_ids=True)
        extractor.reextract = set(reextract or ())
        extractor.system_categories = SYSTEM_CATEGORIES
        extractor.ocr = OcrReader()                       # C9: ids must appear in the image's OCR text
    index = build_evidence_index(ds, universe, extractor)

    requests = ds.all_requests
    subjects = {r["case_id"]: universe.resolve(r["case_id"], r["email"]) for r in requests}

    # ---- intent (the model's first job) ----
    classifier = IntentClassifier(client, cache)
    texts = {r["case_id"]: model_text(r["request_text"]) for r in requests}
    intents: dict[str, IntentResult] = classifier.classify([t for t in texts.values() if t is not None])
    owners = resource_owners(index, ds.activity, universe)
    owner_users = {rid: sorted({e.user_id for e in edges}) for rid, edges in owners.items()}
    contexts: list[CaseContext] = []
    intent_trace: dict[str, dict] = {}
    for r in requests:
        cid = r["case_id"]
        ctx = CaseContext(cid, r, subjects[cid], False)
        rows = classify_request_rows(ds.activity, owner_users, ctx)
        if texts[cid] is None:                     # empty text: nothing to authorize, no model call
            result = IntentResult(None, None, None, None, None, False)
            ctx.is_deletion, path = False, "empty_request_text->NOT_APPLICABLE"
        else:
            result = intents[texts[cid]]
            ctx.is_deletion, path = decide(result, rows)
        contexts.append(ctx)
        # cache_hit is run metadata (kept in the usage ledger); traces hold only decision facts, so they are
        # byte-identical whether a reply came from the network or the cache.
        intent_trace[cid] = {**{k: v for k, v in asdict(result).items() if k != "cache_hit"},
                             "classify_request_rows": rows, "path": path, "is_deletion": ctx.is_deletion,
                             **({"truncated_to": MAX_CLASSIFY_CHARS} if len(r["request_text"]) > MAX_CLASSIFY_CHARS else {})}

    # ---- attribution (global, all cases jointly) ----
    attribution = attribute(contexts, index, ds.activity, universe)
    worker_events: dict[str, list[str]] = {}
    for a in sorted(ds.activity, key=lambda a: (a.event_time, a.activity_id)):
        if a.source_system == WORKER:
            worker_events.setdefault(a.resource_id, []).append(a.event_type)

    catalog = EvidenceCatalog(ds.manifest, ds.activity, ds.audit_log)
    outcomes: dict[str, CaseOutcome] = {}
    for ctx in contexts:
        try:
            outcomes[ctx.case_id] = audit_case(ctx, ds, index, attribution, policy, worker_events, catalog,
                                               intent_trace[ctx.case_id])
        except Exception as exc:            # per-case isolation
            outcomes[ctx.case_id] = CaseOutcome(ctx.case_id, "INCOMPLETE", [], {
                "case_id": ctx.case_id, "intent": intent_trace.get(ctx.case_id),
                "degraded": f"{type(exc).__name__}: {exc}",
                "traceback_tail": traceback.format_exc().splitlines()[-3:]}, degraded="exception")

    rr = RunResult(ds, index, outcomes, attribution, ledger)
    rr.summary = summarise(rr, extractor)
    return rr


def audit_case(ctx: CaseContext, ds: Dataset, index: EvidenceIndex, attribution: AttributionResult, policy: Policy,
               worker_events: dict[str, list[str]], catalog: EvidenceCatalog, intent: dict) -> CaseOutcome:
    req = ctx.request
    trace: dict = {
        "case_id": ctx.case_id,
        "request": {k: req[k] for k in ("email", "requested_at", "reported_status", "reported_at", "audit_at")},
        "input_issues": ds.input_issues.get(ctx.case_id, []),
        "subject": {"user_ids": ctx.subject.user_ids, "resolution": ctx.subject.resolution,
                    "identifiers": {k: dict(sorted(v.items())) for k, v in ctx.subject.identifiers.items() if v}},
        "intent": intent,
    }
    if not ctx.is_deletion:
        trace.update({"status": "NOT_APPLICABLE", "steps": [], "attribution": [], "resources": [], "excluded": []})
        return CaseOutcome(ctx.case_id, "NOT_APPLICABLE", [], trace)
    if trace["input_issues"]:
        trace.update({"status": "INCOMPLETE", "steps": [], "degraded": "input validation failed"})
        return CaseOutcome(ctx.case_id, "INCOMPLETE", [], trace, degraded="input_invalid")
    if not ctx.subject.user_ids or len(ctx.subject.user_ids) > 1:
        trace.update({"status": "INCOMPLETE", "steps": [], "degraded": "subject did not resolve to exactly one user"})
        return CaseOutcome(ctx.case_id, "INCOMPLETE", [], trace, degraded="identity_unresolved")

    attrs = attribution.per_case.get(ctx.case_id, [])
    trace["attribution"] = [{**{k: v for k, v in asdict(a).items() if k != "edges"},
                             "edges": [asdict(e) for e in a.edges]} for a in attrs]
    trace["eliminated"] = attribution.eliminated.get(ctx.case_id, [])
    verdicts, resources, excluded = [], [], []
    for a in attrs:
        if not a.in_scope:
            excluded.append({"resource_id": a.resource_id, "label": a.label, "reason": f"OUT_OF_SCOPE: {a.scope_reason}"})
            continue
        if a.label in ("AMBIGUOUS", "CONFLICT"):
            excluded.append({"resource_id": a.resource_id, "label": a.label, "reason": f"{a.label}: {a.rationale}"})
            continue
        system = a.system_id or index.system_of(a.resource_id)
        if not system:
            excluded.append({"resource_id": a.resource_id, "label": a.label, "reason": "UNKNOWN_SYSTEM"})
            continue
        state = reconstruct(a.resource_id, system, req["audit_at"], index)
        category, basis = policy.category(a.resource_id, system, index, worker_events.get(a.resource_id, []))
        v = policy.verdict(category, basis, state)
        resources.append({"resource_id": a.resource_id, "system_id": system, "attribution": a.label,
                          "state": asdict(state), "category": category, "category_basis": basis,
                          "required_action": v.required_action, "compliant": v.compliant,
                          "operations": [v.finding_op, v.remediation_op, v.verification_op],
                          "proof_file_id": v.proof_file_id, "note": v.note})
        if v.compliant is None:
            excluded.append({"resource_id": a.resource_id, "label": a.label, "reason": v.note})
        else:
            verdicts.append(v)
    status, steps, unproven = build_steps(verdicts)
    # G8 fail closed: a verification is unproven if a rejected evidence file captured after its proof and at or
    # before audit_at could hold a newer observation. Such a case is never COMPLETE.
    dropped = {fid: ef.captured_at for fid, ef in index.files.items() if ef.status in DEGRADE_STATUSES
               and (parse_ts(ef.captured_at) is None or parse_ts(ef.captured_at) <= parse_ts(req["audit_at"]))}
    depends = {}
    for v in verdicts:
        if v.compliant and v.proof_file_id in index.files and dropped:
            proof_t = parse_ts(index.files[v.proof_file_id].captured_at)
            later = sorted(f for f, t in dropped.items() if parse_ts(t) is None or parse_ts(t) > proof_t)
            if later:
                depends[v.resource_id] = later
    fail_closed = bool(depends) and status == "COMPLETE"
    if fail_closed:
        status = "INCOMPLETE"
        steps = [{**s, "evidence_id": ""} for s in steps]          # future checks, not proof
    label_of = {a.resource_id: a.label for a in attrs}
    for v in unproven:
        excluded.append({"resource_id": v.resource_id, "label": label_of.get(v.resource_id),
                         "reason": f"UNPROVEN_IN_COMPLETE: {v.note}"})
    degraded = None
    reason_code = None
    if fail_closed:
        reason_code = "DEPENDS_ON_DROPPED_EVIDENCE"
        degraded = f"verification depends on rejected evidence: {depends}"
    if not steps:
        status = "INCOMPLETE"
        attributable = [a for a in attrs if a.in_scope and a.label in ("CONFIRMED", "INFERRED")]
        # M2(b): nothing can be attributed to this person without risking someone else's data. The row stays
        # INCOMPLETE [] and the file validator accepts it only with this reason code.
        reason_code = "NO_ATTRIBUTABLE_RESOURCE" if not attributable else "NO_VERIFIABLE_RESOURCE"
        degraded = "no attributable resource" if not attributable else "no resource could be verified"
    errors = validate_row(status, serialize(steps), req["audit_at"], catalog, allow_no_finding=fail_closed)
    if errors:
        status, steps, degraded = "INCOMPLETE", [], "validation_failed"
    trace.update({"resources": resources, "excluded": excluded, "status": status, "steps": steps,
                  "validation_errors": errors, "degraded": degraded, "reason_code": reason_code})
    return CaseOutcome(ctx.case_id, status, steps, trace, degraded)


def is_model_failure(outcome: CaseOutcome) -> bool:
    return str((outcome.trace.get("intent") or {}).get("path", "")).startswith("model_failure")


def summarise(rr: RunResult, extractor: ImageExtractor | None) -> dict:
    ds = rr.dataset
    eval_ids = [r["case_id"] for r in ds.requests]
    sample_ids = [r["case_id"] for r in ds.sample_requests]

    def counts(ids: list[str]) -> dict:
        labels: Counter = Counter()
        excluded: Counter = Counter()
        for cid in ids:
            t = rr.outcomes[cid].trace
            for a in t.get("attribution", []):
                labels[a["label"]] += 1
            for e in t.get("excluded", []):
                excluded[e["reason"].split(":")[0]] += 1
        return {
            "cases": len(ids),
            "status": dict(sorted(Counter(rr.outcomes[c].status for c in ids).items())),
            "intent_paths": dict(sorted(Counter((rr.outcomes[c].trace.get("intent") or {}).get("path", "?")
                                                for c in ids).items())),
            "attribution_labels": dict(sorted(labels.items())),
            "excluded_reasons": dict(sorted(excluded.items())),
            # A case whose intent call failed is degraded too: it fell back to NOT_APPLICABLE without a decision.
            "model_failures": sorted(c for c in ids if is_model_failure(rr.outcomes[c])),
            "degraded": sorted(c for c in ids if rr.outcomes[c].degraded or is_model_failure(rr.outcomes[c])),
            "no_attributable_resource": sorted(c for c in ids if rr.outcomes[c].trace.get("reason_code")
                                               == "NO_ATTRIBUTABLE_RESOURCE"),
        }
    files = Counter(f.status for f in rr.index.files.values())
    rejected_files = [{"file_id": f.file_id, "status": f.status, "reason": "; ".join(f.notes)[:200]}
                      for f in sorted(rr.index.files.values(), key=lambda f: f.file_id) if f.status in DEGRADE_STATUSES]
    return {
        "eval": counts(eval_ids), "samples": counts(sample_ids),
        "evidence_files": dict(sorted(files.items())),
        "image_extraction": {"accepted_rows": len(extractor.accepted) if extractor else 0,
                             "dropped_rows": extractor.dropped if extractor else [],
                             "failed_files": extractor.failures if extractor else []},
        "model_failures": {"intent_cases": len(counts(eval_ids + sample_ids)["model_failures"]),
                           "vlm_files_skipped": len(extractor.failures) if extractor else 0,
                           "evidence_files_rejected": len(rejected_files)},
        "evidence_rejected": rejected_files,
        "conflicts": rr.attribution.conflicts,
        "content_conflicts": rr.attribution.content_conflicts,
        "image_rows_accepted": extractor.accepted if extractor else [],
        "image_legacy_replies_upgraded": extractor.legacy_used if extractor else [],
        "image_record_type_claims_rejected": extractor.rejected_categories if extractor else [],
        "ocr_grounding": {"engines": sorted(extractor.ocr.engines_used) if extractor and extractor.ocr else [],
                          "ungrounded": extractor.ungrounded if extractor else [],
                          "per_image": extractor.grounding_report if extractor else {}},
        "account_batches": len(rr.attribution.batches),
    }


def write_outputs(rr: RunResult, output_csv: Path, sample_csv: Path | None, traces_dir: Path | None) -> None:
    def write(path: Path, ids: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(HEADER)
            for cid in sorted(ids, key=case_sort_key):
                o = rr.outcomes[cid]
                w.writerow([cid, o.status, serialize(o.steps)])
    write(output_csv, [r["case_id"] for r in rr.dataset.requests])
    if sample_csv is not None and rr.dataset.sample_requests:
        write(sample_csv, [r["case_id"] for r in rr.dataset.sample_requests])
    if traces_dir is not None:
        traces_dir.mkdir(parents=True, exist_ok=True)
        for cid, o in rr.outcomes.items():
            (traces_dir / f"{cid}.json").write_text(json.dumps(o.trace, ensure_ascii=False, indent=1, sort_keys=True,
                                                               default=str) + "\n", encoding="utf-8")
        (traces_dir / "_run_summary.json").write_text(json.dumps(rr.summary, ensure_ascii=False, indent=1,
                                                                 sort_keys=True, default=str) + "\n", encoding="utf-8")
