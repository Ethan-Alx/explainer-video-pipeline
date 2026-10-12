"""Audit explicitly selected explainer production requirements without changing a plan.

This is a planning/final evidence gate, not a perceptual evaluator. Legacy plans
without production_policy and plans for another channel are not silently upgraded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path


DEFAULT_POLICY = {
    "version": "7.2", "preset_id": "explainer_v72", "channel_id": "example-channel",
    "ordinary_still_target_s": 8, "ordinary_still_review_limit_s": 10,
    "flow_video_limit": 5, "flow_paid_submission_budget": 5,
    "motion": {"minimum_scale_relative_delta": 0.20,
               "minimum_translation_canvas_fraction": 0.08},
    "labels": {"minimum_font_px_at_1080p": 40,
               "minimum_contrast_ratio": 4.5},
}
MAJOR_VIDEO_ERRORS = {"technical_failure", "wrong_action", "wrong_subject", "identity_break",
                      "scientific_misrepresentation", "unusable_geometry", "missing_required_content"}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            value.update(block)
    return value.hexdigest()


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _path(base, value):
    if not _nonempty(value):
        return None
    path = Path(value)
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _is_dynamic(element, shot):
    """Require effective scheduled element change, not a category or list of IDs."""
    if not element or element.get("start_us", 0) >= shot["end_us"] or element.get("end_us", 0) <= shot["start_us"]:
        return False
    if shot["start_us"] < element.get("start_us", 0) < shot["end_us"] or shot["start_us"] < element.get("end_us", 0) < shot["end_us"]:
        return True
    animation = element.get("animation", {})
    points = animation.get("keyframes", [])
    defaults = {"scale": 1, "dx_px": 0, "dy_px": 0, "alpha": 1}
    poses = {tuple(point.get(key, default) for key, default in defaults.items()) for point in points}
    if len(poses) > 1:
        return True
    return any(_number(animation.get(key)) and animation[key] > 0 for key in ("entry_us", "exit_us")) or (
        _number(animation.get("move_y_px")) and animation["move_y_px"] != 0
    ) or bool(animation.get("loop", {}).get("amplitude_px", 0))


def _effective_at(element, shot, relative_at_us):
    """Check that a declared semantic stage has an actual change at that time."""
    absolute_at = shot["start_us"] + relative_at_us
    start, end = element.get("start_us", 0), element.get("end_us", 0)
    if abs(absolute_at - start) <= 100000 or abs(absolute_at - end) <= 100000:
        return True
    animation = element.get("animation", {})
    points = animation.get("keyframes", [])
    local_at = absolute_at - start
    defaults = {"scale": 1, "dx_px": 0, "dy_px": 0, "alpha": 1}
    for first, last in zip(points, points[1:]):
        if first.get("at_us", 0) <= local_at <= last.get("at_us", 0):
            if any(first.get(key, default) != last.get(key, default) for key, default in defaults.items()):
                return True
    entry, exit_us = animation.get("entry_us", 0), animation.get("exit_us", 0)
    if _number(entry) and 0 <= local_at <= entry and entry > 0:
        return True
    if _number(exit_us) and end - start - exit_us <= local_at <= end - start and exit_us > 0:
        return True
    return start <= absolute_at <= end and bool(animation.get("loop", {}).get("amplitude_px", 0))


def _contrast(first, second):
    def luminance(color):
        if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            return None
        values = [int(color[index:index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in values]
        return sum(weight * value for weight, value in zip((.2126, .7152, .0722), linear))
    a, b = luminance(first), luminance(second)
    if a is None or b is None:
        return None
    return (max(a, b) + .05) / (min(a, b) + .05)


def _camera_metrics(shot):
    # Use the actual writer's coordinate convention and authored-camera mapping.
    from draft_engine import camera_points
    points = camera_points(shot)
    if not points or not all(all(_number(point.get(key)) for key in ("scale", "x", "y")) for point in points):
        raise ValueError("camera points must contain finite scale/x/y")
    scales = [point["scale"] for point in points]
    if min(scales) <= 0:
        raise ValueError("camera scale must be positive")
    xs, ys = [point["x"] for point in points], [point["y"] for point in points]
    return {
        "start": {key: points[0][key] for key in ("scale", "x", "y")},
        "end": {key: points[-1][key] for key in ("scale", "x", "y")},
        "scale_endpoint_relative_delta": abs(scales[-1] / scales[0] - 1),
        "scale_range_relative_delta": max(scales) / min(scales) - 1,
        "x_endpoint_canvas_fraction": abs(xs[-1] - xs[0]) / 2,
        "y_endpoint_canvas_fraction": abs(ys[-1] - ys[0]) / 2,
        "x_range_canvas_fraction": (max(xs) - min(xs)) / 2,
        "y_range_canvas_fraction": (max(ys) - min(ys)) / 2,
        "coordinate_note": "native x/y units are half a canvas; scale measured relative to the smallest scale; screen-space translation excludes object-anchor changes from zoom",
    }


def audit_plan(plan, base=None, stage="planning"):
    """Return deterministic findings; checks do not certify taste or real playback."""
    if stage not in ("planning", "final"):
        raise ValueError("stage must be planning or final")
    base = Path(base or ".").resolve()
    project_identity = plan.get("project_identity", {})
    if isinstance(project_identity, dict) and _nonempty(project_identity.get("project_root")):
        base = Path(project_identity["project_root"]).resolve()
    report = {"schema_version": 1, "stage": stage, "status": "not_applicable",
              "errors": [], "warnings": [], "shot_metrics": [], "label_checks": [],
              "boundary": "Numerical screening and declared evidence only; no automatic perceptual/motion/readability certification. No plan, source, or accepted draft is changed."}
    if "production_policy" not in plan:
        report["reason"] = "Legacy plan has no production_policy; accepted historical drafts are not reclassified."
        return report
    policy = plan["production_policy"]
    if not isinstance(policy, dict):
        report["errors"].append({"code": "policy_schema", "message": "production_policy must be an object"})
        report["status"] = "failed"
        return report
    if not _nonempty(policy.get("channel_id")):
        report["errors"].append({"code": "policy_schema", "message": "Explicit production_policy requires a nonempty channel_id"})
        report["status"] = "failed"
        return report
    if policy.get("preset_id") != "explainer_v72":
        report["reason"] = "Generic quality gate requires explicit preset_id explainer_v72; this channel has not opted in."
        return report
    report["policy_version"] = policy.get("version", "7.2")
    evidence_cache = {}

    def finding(code, message, identity=None, warning=False):
        item = {"code": code, "message": message}
        if identity is not None:
            item["id"] = identity
        report["warnings" if warning else "errors"].append(item)

    def evidence(value, identity, code):
        path = _path(base, value)
        if path and path.is_file():
            # Whole-episode previews may serve hundreds of shot reviews. Hash
            # unchanged bytes once per audit rather than rereading gigabytes.
            metadata = path.stat()
            cache_key = (str(path), metadata.st_size, metadata.st_mtime_ns)
            if cache_key not in evidence_cache:
                evidence_cache[cache_key] = {"path": str(path), "sha256": sha256(path)}
            return evidence_cache[cache_key]
        finding(code, "Declared final preview is missing or not a file", identity, warning=stage == "planning")
        return None

    def setting(key):
        value = policy.get(key, DEFAULT_POLICY[key])
        if not _number(value) or value <= 0:
            finding("policy_schema", key + " must be a positive finite number")
            return DEFAULT_POLICY[key]
        return value

    target, limit = setting("ordinary_still_target_s"), setting("ordinary_still_review_limit_s")
    video_limit, paid_budget = setting("flow_video_limit"), setting("flow_paid_submission_budget")
    if target > limit:
        finding("policy_schema", "ordinary_still_target_s must not exceed ordinary_still_review_limit_s")
    motion_policy, labels_policy = policy.get("motion", {}), policy.get("labels", {})
    if not isinstance(motion_policy, dict) or not isinstance(labels_policy, dict):
        finding("policy_schema", "motion and labels must be objects")
        motion_policy, labels_policy = {}, {}
    thresholds = {}
    for key, default in DEFAULT_POLICY["motion"].items():
        value = motion_policy.get(key, default)
        if not _number(value) or value <= 0:
            finding("policy_schema", key + " must be positive and finite")
            value = default
        thresholds[key] = value
    for key, default in DEFAULT_POLICY["labels"].items():
        value = labels_policy.get(key, default)
        if not _number(value) or value <= 0:
            finding("policy_schema", key + " must be positive and finite")
            value = default
        thresholds[key] = value
    shots, elements = plan.get("shots", []), plan.get("mg", [])
    if not isinstance(shots, list) or not isinstance(elements, list):
        finding("plan_schema", "shots and mg must be lists")
        shots, elements = [], []
    if not shots:
        finding("plan_schema", "No authored shots to audit")
    element_map = {element.get("id"): element for element in elements if isinstance(element, dict)}
    flow_shots = []
    for shot in shots:
        if not isinstance(shot, dict):
            finding("shot_schema", "shot must be an object")
            continue
        identity = shot.get("id", "missing-id")
        a, b = shot.get("start_us"), shot.get("end_us")
        if not _number(a) or not _number(b) or b <= a:
            finding("shot_schema", "shot requires finite increasing start_us/end_us", identity)
            continue
        duration_s = (b - a) / 1000000
        if shot.get("media_type", "image") == "video":
            generation = shot.get("generation", {})
            if not isinstance(generation, dict):
                finding("video_schema", "generation must be an object", identity)
                generation = {}
            if str(generation.get("provider", "flow")).lower() == "flow":
                flow_shots.append(shot)
            continue
        presentation = shot.get("presentation", {})
        if not isinstance(presentation, dict):
            finding("shot_schema", "presentation must be an object", identity)
            presentation = {}
        changes = presentation.get("state_changes", [])
        valid_changes = []
        if not isinstance(changes, list):
            finding("mechanism_schema", "state_changes must be a list", identity)
            changes = []
        for change in changes:
            if not isinstance(change, dict):
                continue
            at = change.get("at_us")
            refs = change.get("element_ids", [])
            if _number(at) and 0 < at < b - a and _nonempty(change.get("description")) and isinstance(refs, list) and refs:
                if all(ref in element_map and _is_dynamic(element_map[ref], shot) and _effective_at(element_map[ref], shot, at) for ref in refs):
                    valid_changes.append(change)
        mechanism = presentation.get("kind") == "mechanism" and len({change["at_us"] for change in valid_changes}) >= 2
        if presentation.get("kind") == "mechanism" and not mechanism:
            finding("mechanism_exemption_invalid", "Mechanism duration exemption needs at least two distinct timed semantic state changes backed by effective MG change", identity, warning=duration_s <= limit)
        exception = shot.get("duration_exception", {})
        exception_reason = exception.get("reason") if isinstance(exception, dict) else None
        if duration_s > limit and not mechanism and not _nonempty(exception_reason):
            finding("ordinary_still_too_long", f"Ordinary still lasts {duration_s:.3f}s (> {limit:g}s); split meaningful detail/state shots or record a specific exception", identity)
        elif duration_s > target and not mechanism:
            finding("ordinary_still_over_target", f"Ordinary still lasts {duration_s:.3f}s; target <= {target:g}s. An explicit exception does not create new information.", identity, warning=True)
        try:
            metrics = _camera_metrics(shot)
        except (TypeError, KeyError, ValueError) as exc:
            finding("camera_schema", str(exc), identity)
            continue
        significant = (metrics["scale_range_relative_delta"] + 1e-9 >= thresholds["minimum_scale_relative_delta"] or
                       max(metrics["x_range_canvas_fraction"], metrics["y_range_canvas_fraction"]) + 1e-9 >= thresholds["minimum_translation_canvas_fraction"])
        review = shot.get("motion_review", {})
        if not isinstance(review, dict):
            finding("motion_schema", "motion_review must be an object", identity)
            review = {}
        dynamic_refs = review.get("dynamic_element_ids", [])
        dynamic = isinstance(dynamic_refs, list) and bool(dynamic_refs) and all(ref in element_map and _is_dynamic(element_map[ref], shot) for ref in dynamic_refs)
        deliberate = _nonempty(review.get("reason")) and review.get("reviewed") is True and review.get("focus_protected") is True
        preview = None
        if stage == "final":
            preview = evidence(review.get("continuous_preview"), identity, "motion_final_preview_missing")
            if review.get("reviewed") is not True or review.get("focus_protected") is not True:
                finding("motion_final_review_missing", "Final still requires declared continuous-preview review and focus protection; numerical movement alone does not prove visible motion", identity)
        if not significant and not dynamic:
            accepted_exception = stage == "final" and deliberate and preview is not None
            finding("weak_camera_motion", "Numerical movement is weak; plan a visible push/pull/pan or effective foreground action. A deliberate final exception needs a reason and actual continuous-preview review.", identity, warning=stage == "planning" or accepted_exception)
        metrics.update({"id": identity, "duration_s": duration_s, "mechanism_duration_exempt": mechanism,
                        "effective_foreground_motion": dynamic, "passes_numerical_motion_screen": significant,
                        "declared_final_review": bool(preview and review.get("reviewed") is True),
                        "exception_reason": review.get("reason")})
        report["shot_metrics"].append(metrics)
        label_review = shot.get("label_review", {})
        if isinstance(label_review, dict) and label_review.get("omitted") is True:
            if not _nonempty(label_review.get("reason")) or label_review.get("alternative") != "object_highlight":
                finding("omitted_label_schema", "Omitted labels need a reason and alternative object_highlight", identity)
            refs = label_review.get("element_ids", [])
            if not isinstance(refs, list) or not refs or not all(ref in element_map for ref in refs):
                finding("omitted_label_target", "Omitted label fallback must map to actual highlight elements", identity)

    if len(flow_shots) > video_limit:
        finding("flow_video_limit", f"{len(flow_shots)} planned Flow video shots exceeds this episode limit {video_limit:g}")
    ledger_path = _path(base, policy.get("flow_submission_ledger"))
    rows = []
    if ledger_path:
        if not ledger_path.is_file():
            finding("flow_ledger_missing", "Declared Flow submission ledger is missing", warning=stage == "planning")
        else:
            try:
                ledger = read_json(ledger_path)
                rows = ledger if isinstance(ledger, list) else ledger.get("submissions", [])
                if not isinstance(rows, list):
                    raise ValueError("submissions must be a list")
                if project_identity:
                    ledger_identity = ledger.get("project_identity", {}) if isinstance(ledger, dict) else {}
                    if any(not _nonempty(project_identity.get(key)) or ledger_identity.get(key) != project_identity.get(key) for key in ("project_id", "project_instance_id")):
                        finding("flow_ledger_cross_project", "Ledger must match project_id/project_instance_id; all runs of this episode still share one budget")
                report["flow_ledger"] = {"path": str(ledger_path), "sha256": sha256(ledger_path)}
            except (OSError, ValueError, AttributeError) as exc:
                finding("flow_ledger_schema", str(exc))
                rows = []
    elif flow_shots:
        finding("flow_ledger_missing", "Flow generation ledger must be bound to the plan, including failed/retried paid submits", warning=stage == "planning")
    paid_rows, seen_tasks, seen_shots = [], set(), set()
    for row in rows:
        if not isinstance(row, dict):
            finding("flow_ledger_schema", "Submission row must be an object")
            continue
        if str(row.get("provider", "flow")).lower() != "flow" or row.get("media_type", "video") != "video":
            continue
        task_id = row.get("task_id")
        shot_id = row.get("shot_id")
        if not _nonempty(shot_id):
            finding("flow_ledger_schema", "Each Flow submission needs shot_id", task_id)
        elif shot_id in seen_shots and row.get("is_retry") is not True:
            finding("flow_retry_undeclared", "Repeated submission for the same shot must be recorded as a retry", task_id)
        seen_shots.add(shot_id)
        if not _nonempty(task_id):
            finding("flow_ledger_schema", "Each Flow submission needs an actual unique task_id")
        elif task_id in seen_tasks:
            finding("flow_task_duplicate", "Duplicate Flow task entry cannot inflate or disguise accounting", task_id)
        else:
            seen_tasks.add(task_id)
        if not isinstance(row.get("paid"), bool):
            finding("flow_paid_unknown", "Each submission must explicitly record paid true/false; unknown charging still consumes the conservative submission budget", task_id)
        if row.get("paid") is not False:
            paid_rows.append(row)
        if row.get("is_retry") is True and (not _nonempty(row.get("major_error_reason")) or row.get("major_error_code") not in MAJOR_VIDEO_ERRORS):
            finding("flow_retry_minor_or_undocumented", "Retry requires a concrete major functional error; stylistic polishing is not sufficient", task_id)
    if len(paid_rows) > paid_budget:
        finding("flow_paid_budget", f"{len(paid_rows)} paid/unknown Flow video submits exceeds episode budget {paid_budget:g}; failures and retries count")
    usable_tasks = {row.get("task_id") for row in rows if isinstance(row, dict) and row.get("status") in ("accepted", "used") and str(row.get("provider", "flow")).lower() == "flow"}
    unsubmitted = sum(1 for shot in flow_shots if shot.get("generation", {}).get("task_id") not in usable_tasks)
    if stage == "planning" and len(paid_rows) + unsubmitted > paid_budget:
        finding("flow_remaining_budget", "Existing paid/unknown submits plus planned unfinished clips exceed the episode submission budget; reduce the remaining video plan instead of silently resubmitting")
    if stage == "final":
        ledger_map = {row.get("task_id"): row for row in rows if isinstance(row, dict)}
        for shot in flow_shots:
            generation = shot.get("generation", {})
            row = ledger_map.get(generation.get("task_id"))
            if not row or str(row.get("provider", "flow")).lower() != "flow" or row.get("media_type", "video") != "video" or row.get("shot_id") != shot.get("id") or not _nonempty(row.get("asset_id")) or row.get("asset_id") != generation.get("asset_id"):
                finding("flow_asset_mapping", "Final Flow video must map shot_id/task_id/asset_id to the submission ledger", shot.get("id"))
            elif row.get("status") not in ("accepted", "used"):
                finding("flow_asset_status", "Final used Flow video must have accepted/used ledger status", shot.get("id"))
    report["flow_budget"] = {"planned_video_shots": len(flow_shots), "video_limit": video_limit,
                             "paid_or_unknown_submissions": len(paid_rows), "paid_submission_budget": paid_budget,
                             "planned_unfinished_video_shots": unsubmitted}

    for element in elements:
        if not isinstance(element, dict) or not (element.get("type") == "text" or element.get("contains_explanatory_text") is True):
            continue
        identity = element.get("id", "missing-id")
        readability = element.get("readability", {})
        if not isinstance(readability, dict):
            readability = {}
        background = readability.get("background", {})
        backing_contrast = _contrast(element.get("text_color", readability.get("text_color")), background.get("color")) if isinstance(background, dict) else None
        has_background = isinstance(background, dict) and backing_contrast is not None and backing_contrast >= thresholds["minimum_contrast_ratio"] and _number(background.get("opacity")) and background["opacity"] >= .90 and _number(background.get("padding_px")) and background["padding_px"] >= 8
        contrast = readability.get("contrast_ratio")
        has_contrast = readability.get("contrast_reviewed") is True and _number(contrast) and contrast >= thresholds["minimum_contrast_ratio"]
        if not has_background and not has_contrast:
            finding("label_contrast_unplanned", "Label needs a local high-opacity backing or verified contrast at its actual placement; otherwise omit and highlight the object", identity, warning=stage == "planning")
        font_px = readability.get("font_px_at_1080p")
        if not _number(font_px) or font_px < thresholds["minimum_font_px_at_1080p"]:
            finding("label_size_unverified", "Declare readable final 1080p font size; proxy/native text_size units alone are not display-size evidence", identity, warning=stage == "planning")
        preview = None
        if stage == "final":
            if has_background:
                backing = element_map.get(background.get("element_id"))
                box, backing_box = element.get("bbox_px"), backing.get("bbox_px") if backing else None
                covers = (backing and backing.get("type") == "png" and
                          backing.get("start_us", 0) <= element.get("start_us", 0) and
                          backing.get("end_us", 0) >= element.get("end_us", 0) and
                          isinstance(box, list) and isinstance(backing_box, list) and len(box) == len(backing_box) == 4 and
                          backing_box[0] <= box[0] and backing_box[1] <= box[1] and backing_box[2] >= box[2] and backing_box[3] >= box[3])
                embedded = element.get("type") == "png" and background.get("rendered_in_asset") is True
                if not covers and not embedded:
                    finding("label_backing_not_rendered", "Final backing must map to a real PNG MG element covering label time/layout, or be declared embedded in the text PNG. Readability metadata is not rendered by the writer.", identity)
            preview = evidence(readability.get("final_preview"), identity, "label_final_preview_missing")
            if readability.get("reviewed") is not True:
                finding("label_final_review_missing", "Final label requires declared full-size composite review, including moving background/crop and edge protection", identity)
        report["label_checks"].append({"id": identity, "local_backing_declared": has_background,
                                       "backing_color_contrast_screen": backing_contrast,
                                       "contrast_review_declared": has_contrast, "font_px_at_1080p": font_px,
                                       "declared_final_preview": preview})
    report["status"] = "failed" if report["errors"] else "passed_with_warnings" if report["warnings"] else "passed"
    return report


def audit_file(path, stage="planning"):
    path = Path(path).resolve()
    result = audit_plan(read_json(path), path.parent, stage)
    result.update({"plan": str(path), "plan_sha256": sha256(path)})
    return result


def require_policy(plan, base=None, stage="planning"):
    """Convenience hook for pipeline validate/build; final audit remains explicit."""
    report = audit_plan(plan, base, stage)
    if report["errors"]:
        messages = "; ".join(f"{row['code']} ({row.get('id', 'plan')}): {row['message']}" for row in report["errors"][:8])
        raise ValueError("Production policy audit failed: " + messages)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--plan", required=True)
    audit.add_argument("--stage", choices=("planning", "final"), default="planning")
    audit.add_argument("--report")
    args = parser.parse_args(argv)
    try:
        result = audit_file(args.plan, args.stage)
    except (OSError, ValueError, TypeError) as exc:
        result = {"status": "failed", "errors": [{"code": "input", "message": str(exc)}], "warnings": []}
    if args.report:
        write_json(args.report, result)
    print(json.dumps({"status": result["status"], "stage": args.stage,
                      "errors": len(result.get("errors", [])), "warnings": len(result.get("warnings", [])),
                      "report": args.report, "reason": result.get("reason")}, ensure_ascii=False))
    return 1 if result.get("errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
