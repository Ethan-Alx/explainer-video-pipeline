"""Check science ledger links and declared review state; never verify scientific truth."""
import argparse
import hashlib
import json
from pathlib import Path

REPRESENTATIONS = {"observation", "scientific_model", "schematic", "metaphor", "uncertain", "technical_fixture"}
REVIEW_STATES = {"pending", "reviewed", "uncertain", "rejected"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def check(plan, ledger, base=Path("."), ledger_sha=None):
    errors, pending, notes = [], [], []
    if not isinstance(ledger, dict):
        return {"ok": False, "errors": ["science_source_ledger must be an object"], "pending": [], "review_state_complete": False, "scientific_truth_verified_by_tool": False}
    if plan.get("science_source_ledger_sha256"):
        actual_sha = ledger_sha or hashlib.sha256(json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        if actual_sha != plan["science_source_ledger_sha256"]:
            errors.append("Frozen science ledger SHA mismatch; bind an explicitly selected new revision")
    tables = {}
    for name in ("sources", "claims", "subjects"):
        rows = ledger.get(name, [])
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            errors.append(name + " must be a list of objects")
            rows = []
        ids = [row.get("id") for row in rows]
        if any(not isinstance(i, str) or not i.strip() for i in ids):
            errors.append(name + " needs nonempty string IDs")
        if len(set(str(i) for i in ids)) != len(ids):
            errors.append(name + " has duplicate IDs")
        tables[name] = {row.get("id"): row for row in rows if isinstance(row.get("id"), str)}

    def links(row, field, table, label):
        refs = row.get(field, [])
        if not isinstance(refs, list) or any(not isinstance(x, str) for x in refs):
            errors.append(label + ": " + field + " must be a list of IDs")
            return []
        for ref in refs:
            if ref not in tables[table]:
                errors.append(label + ": unknown " + table + " ID " + ref)
        return refs

    for sid, src in tables["sources"].items():
        if not src.get("url") and not src.get("file"):
            errors.append(sid + ": source needs url or file")
        if src.get("file"):
            path = Path(src["file"])
            if not path.is_absolute():
                path = base / path
            if not path.is_file():
                errors.append(sid + ": source file missing")
            elif src.get("sha256") and hashlib.sha256(path.read_bytes()).hexdigest() != src["sha256"]:
                errors.append(sid + ": source file SHA mismatch")
    for cid, claim in tables["claims"].items():
        refs = links(claim, "source_ids", "sources", cid)
        status = claim.get("review_status", "pending")
        if status not in REVIEW_STATES:
            errors.append(cid + ": invalid review_status")
        if not claim.get("text"):
            errors.append(cid + ": claim text missing")
        if status == "reviewed" and (not refs or not claim.get("review_note")):
            errors.append(cid + ": reviewed claim needs sources and review_note")

    fixture = plan.get("technical_fixture") is True
    shots = plan.get("shots", [])
    if not isinstance(shots, list) or any(not isinstance(s, dict) for s in shots):
        errors.append("shots must be a list of objects")
        shots = []
    for shot in shots:
        label = str(shot.get("id", "unnamed shot"))
        links(shot, "subject_ids", "subjects", label)
        visual = shot.get("science_visual")
        if visual is None and fixture:
            continue
        if not isinstance(visual, dict):
            pending.append(label + ": classify science_visual before scientific review")
            continue
        rep = visual.get("representation")
        if rep not in REPRESENTATIONS:
            errors.append(label + ": unknown representation")
        if rep == "technical_fixture" and not fixture:
            errors.append(label + ": technical_fixture requires explicit plan flag")
        claims = links(visual, "claim_ids", "claims", label)
        links(visual, "source_ids", "sources", label)
        if rep in {"observation", "scientific_model"} and not claims:
            pending.append(label + ": factual visual needs a linked claim")
        if rep in {"metaphor", "uncertain"} and not visual.get("uncertainty"):
            pending.append(label + ": clarify metaphor/uncertainty in uncertainty field")
        for cid in claims:
            claim = tables["claims"].get(cid, {})
            status = claim.get("review_status", "pending")
            if status == "rejected":
                errors.append(label + ": uses rejected claim " + cid)
            elif status != "reviewed":
                pending.append(label + ": claim " + cid + " is " + status)
        if visual.get("precise_scale") is True and not visual.get("scale_note"):
            pending.append(label + ": precise scale needs scale_note")
        if visual.get("anatomy_required") is True and visual.get("anatomy_check") != "reviewed":
            pending.append(label + ": anatomy inspection pending")
    if not shots:
        pending.append("No production shots; preproduction is not a scientific production review")
    if fixture:
        notes.append("Technical fixture only; excluded from scientific production readiness")
    notes.append("Checks links and declared review state only. Sources and rendered anatomy still require human/model source review; no scientific truth certification.")
    return {"ok": not errors, "errors": errors, "pending": sorted(set(pending)), "notes": notes,
            "technical_fixture": fixture, "review_state_complete": not errors and not pending and not fixture,
            "scientific_truth_verified_by_tool": False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--plan", required=True)
    p.add_argument("--ledger")
    p.add_argument("--report")
    p.add_argument("--require-reviewed", action="store_true", help="fail for pending production reviews; does not certify truth")
    args = p.parse_args()
    plan_path = Path(args.plan).resolve()
    try:
        plan = read_json(plan_path)
        value = args.ledger or plan.get("science_source_ledger", {})
        base = plan_path.parent
        ledger_sha = None
        if isinstance(value, str):
            ledger_path = Path(value)
            if not ledger_path.is_absolute():
                ledger_path = base / ledger_path
            ledger, base = read_json(ledger_path), ledger_path.parent
            ledger_sha = hashlib.sha256(ledger_path.read_bytes()).hexdigest()
        else:
            ledger = value
        result = check(plan, ledger, base, ledger_sha)
    except (OSError, ValueError, TypeError) as exc:
        result = {"ok": False, "errors": [str(exc)], "pending": [], "review_state_complete": False, "scientific_truth_verified_by_tool": False}
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.report:
        Path(args.report).write_text(output, encoding="utf-8")
    print(output, end="")
    raise SystemExit(0 if result["ok"] and (not args.require_reviewed or result["review_state_complete"]) else 2)


if __name__ == "__main__":
    main()
