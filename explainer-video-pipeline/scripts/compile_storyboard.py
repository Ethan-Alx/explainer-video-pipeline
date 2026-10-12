"""Compile authored cue ranges; bind verified downloads without guessing scene order."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve(root, value):
    p = Path(value)
    return p.resolve() if p.is_absolute() else (Path(root) / p).resolve()


def freeze_science_ledger(plan, value, base, expected_sha=None, require_file=False):
    """Freeze a declared revision and resolve paths, without approving any claim."""
    ledger = copy.deepcopy(value)
    if isinstance(ledger, str):
        path = resolve(base, ledger)
        if require_file and not path.is_file():
            raise ValueError('Science ledger file missing: ' + str(path))
        digest = sha256(path) if path.is_file() else None
        if expected_sha and digest != expected_sha:
            raise ValueError('Frozen science ledger changed')
        plan['science_source_ledger'] = str(path)
        if digest:
            plan['science_source_ledger_sha256'] = digest
            plan['science_source_ledger_sha256_kind'] = 'file-bytes'
            plan['science_source_ledger_authored_sha256'] = digest
            plan['science_source_ledger_authored_sha256_kind'] = 'file-bytes'
    elif isinstance(ledger, dict):
        authored = hashlib.sha256(json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        if expected_sha and authored != expected_sha:
            raise ValueError('Frozen embedded science ledger changed')
        for row in ledger.get('sources', []):
            if isinstance(row, dict) and isinstance(row.get('file'), str) and row['file']:
                row['file'] = str(resolve(base, row['file']))
        digest = hashlib.sha256(json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
        plan['science_source_ledger'] = ledger
        plan['science_source_ledger_sha256'] = digest
        plan['science_source_ledger_sha256_kind'] = 'canonical-json'
        plan['science_source_ledger_authored_sha256'] = authored
        plan['science_source_ledger_authored_sha256_kind'] = 'canonical-json'
    else:
        raise ValueError('Science ledger must be an object or a file path')


def parse_srt(path):
    text = Path(path).read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    stamp = re.compile(r"(\d+):(\d{2}):(\d{2})[,\.](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[,\.](\d{3})(?:\s+.*)?$")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise ValueError("Invalid SRT block: expected index, timestamp, and text")
        m = stamp.fullmatch(lines[1].strip())
        if not m:
            raise ValueError("Invalid SRT timestamp at index " + lines[0])
        v = [int(x) for x in m.groups()]
        if v[1] >= 60 or v[2] >= 60 or v[5] >= 60 or v[6] >= 60:
            raise ValueError("Invalid SRT minute/second")
        start = ((v[0] * 60 + v[1]) * 60 + v[2]) * 1000000 + v[3] * 1000
        end = ((v[4] * 60 + v[5]) * 60 + v[6]) * 1000000 + v[7] * 1000
        body = "\n".join(lines[2:])
        if start >= end or not body.strip() or (cues and start < cues[-1]["start_us"]):
            raise ValueError("Invalid SRT ordering/duration/text")
        cues.append({"ordinal": len(cues) + 1, "source_index": int(lines[0]), "start_us": start, "end_us": end, "text": body})
    if not cues:
        raise ValueError("Empty SRT")
    return cues


def freeze_sources(plan, root):
    sources = plan["sources"]
    for key in ("audio", "srt", "manuscript"):
        if not sources.get(key):
            continue
        p = resolve(root, sources[key])
        if not p.is_file():
            raise ValueError("Missing source: " + str(p))
        digest = sha256(p)
        if sources.get(key + "_sha256") and digest != sources[key + "_sha256"]:
            raise ValueError("Frozen source changed: " + key)
        sources[key] = str(p)
        sources[key + "_sha256"] = digest


def compile_plan(args):
    project = Path(args.project).resolve()
    base_path = resolve(project, args.base_plan)
    author_path = resolve(project, args.storyboard)
    base = read_json(base_path)
    author = read_json(author_path)
    identity = base.get('project_identity', {})
    if identity.get('project_instance_id'):
        project_root = Path(identity['project_root']).resolve()
        if project != project_root or not base_path.is_relative_to(project_root) or not author_path.is_relative_to(project_root):
            raise ValueError('Storyboard/base plan is outside its owned project')
    freeze_sources(base, base_path.parent)
    cues = parse_srt(base["sources"]["srt"])
    duration = base["canvas"]["duration_us"]
    if type(duration) is not int or duration < cues[-1]["end_us"]:
        raise ValueError("Audio duration must cover the original subtitle tail")
    shots = author.get("shots", [])
    if not shots:
        raise ValueError("Author storyboard needs non-empty shots")
    ranges, ids = [], set()
    next_cue = 1
    for shot in shots:
        scene = shot.get("scene_id", "")
        if not re.fullmatch(r"S\d{3,}", scene) or scene in ids:
            raise ValueError("Each scene needs a unique stable ID such as S001")
        ids.add(scene)
        first, last = shot.get("cue_start"), shot.get("cue_end")
        if type(first) is not int or type(last) is not int or first != next_cue or not first <= last <= len(cues):
            raise ValueError("Cue ranges must cover each original cue once, in order: " + scene)
        if not isinstance(shot.get("prompt"), str) or not shot["prompt"].strip():
            raise ValueError("Missing complete prompt: " + scene)
        refs = []
        for ref in shot.get("reference_files", []):
            if not isinstance(ref, dict) or not ref.get("file_path"):
                raise ValueError("Reference requires file_path and declared purpose/character_id")
            item = copy.deepcopy(ref)
            p = resolve(author_path.parent, item["file_path"])
            if not p.is_file():
                raise ValueError("Missing reference: " + str(p))
            item["file_path"] = str(p)
            item["sha256"] = sha256(p)
            refs.append(item)
        ranges.append((shot, first, last, refs))
        next_cue = last + 1
    if next_cue != len(cues) + 1:
        raise ValueError("Uncovered subtitle cues at the end")
    output = resolve(project, args.output)
    shot_file = project / "shots.json"
    prompt_file = project / "分镜提示词.md"
    report_file = project / "coverage.json"
    for p in (output, shot_file, prompt_file, report_file):
        if p.exists() and not args.replace:
            raise ValueError("Output exists; choose a new revision or use --replace before generation: " + str(p))
    queue = project / "image_jobs.json"
    if args.replace and queue.exists():
        q = read_json(queue)
        if any(j.get("status", "pending") != "pending" or j.get("attempts") or j.get("history") for j in q.get("jobs", [])):
            raise ValueError("Executed image queue exists; create a new revision instead of resetting it")
    production, image_shots, text_lines = [], [], ["# 分镜提示词", "", "时间来自原SRT；分组仅用于批量执行。", ""]
    for i, (shot, first, last, refs) in enumerate(ranges):
        start = 0 if i == 0 else cues[first - 1]["start_us"]
        end = duration if i == len(ranges) - 1 else cues[ranges[i + 1][1] - 1]["start_us"]
        if start >= end:
            raise ValueError("Non-positive scene duration: " + shot["scene_id"])
        prompt_hash = hashlib.sha256(shot["prompt"].encode("utf-8")).hexdigest()
        item = {"id": shot["scene_id"], "start_us": start, "end_us": end, "image": None,
                "cue_start": first, "cue_end": last, "prompt_sha256": prompt_hash,
                "visual_purpose": shot.get("visual_purpose", ""), "technique_ids": shot.get("technique_ids", []),
                "protection": {"reviewed": False, "regions": []}}
        for key in ("camera", "incoming_transition", "subject_ids", "science_visual", "reuse_review", "presentation", "duration_exception", "motion_review", "label_review", "generation"):
            if key in shot:
                item[key] = copy.deepcopy(shot[key])
        if item.get('reuse_review',{}).get('evidence_file'):
            item['reuse_review']['evidence_file']=str(resolve(author_path.parent,item['reuse_review']['evidence_file']))
        production.append(item)
        group = shot.get("group") or "G%03d" % (i // args.group_size + 1)
        image_item = {"scene_id": shot["scene_id"], "prompt": shot["prompt"], "prompt_sha256": prompt_hash,
                      "reference_files": refs, "group": group, "visual_purpose": item["visual_purpose"],
                      "technique_ids": item["technique_ids"]}
        for key in ("subject_ids", "science_visual"):
            if key in shot:
                image_item[key] = copy.deepcopy(shot[key])
        image_shots.append(image_item)
        text_lines.extend(["## %s · %s · %.3f—%.3fs" % (group, shot["scene_id"], start / 1e6, end / 1e6), "",
                           "原cue：%s—%s；方法：%s" % (first, last, ", ".join(item["technique_ids"])), "", shot["prompt"], ""])
    if "render_subtitles" in author:
        if type(author["render_subtitles"]) is not bool:
            raise ValueError("render_subtitles must be a boolean")
        base["render_subtitles"] = author["render_subtitles"]
    base["shots"] = production
    for kind in ("mg", "sfx"):
        base[kind] = copy.deepcopy(author.get(kind, []))
        for item in base[kind]:
            if item.get("asset"):
                item["asset"] = str(resolve(author_path.parent, item["asset"]))
    base["storyboard_source_sha256"] = sha256(author_path)
    base["methods_adopted"] = author.get("methods_adopted", [])
    if "science_source_ledger" in author:
        freeze_science_ledger(base, author['science_source_ledger'], author_path.parent, author.get('science_source_ledger_sha256'))
    if "technical_fixture" in author:
        base["technical_fixture"] = copy.deepcopy(author["technical_fixture"])
    write_json(output, base)
    write_json(shot_file, {"schema_version": 1, "project_identity": copy.deepcopy(base.get('project_identity', {})), "shots": image_shots})
    prompt_file.write_text("\n".join(text_lines), encoding="utf-8")
    report = {"status": "cue_coverage_passed", "cue_count": len(cues), "scene_count": len(shots), "duration_us": duration,
              "source_srt_sha256": base["sources"]["srt_sha256"], "image_generation_executed": False,
              "native_visual_verified": False, "outputs": [str(output), str(shot_file), str(prompt_file)]}
    write_json(report_file, report)
    return report


def bind_images(args):
    plan_path, queue_path = Path(args.plan).resolve(), Path(args.queue).resolve()
    plan, queue = read_json(plan_path), read_json(queue_path)
    identity = plan.get('project_identity', {})
    if identity.get('project_instance_id'):
        queue_identity = queue.get('project_identity', {})
        for key in ('project_instance_id', 'run_id'):
            if queue_identity.get(key) != identity.get(key):
                raise ValueError('Asset queue ownership mismatch: ' + key)
        project_root = Path(identity['project_root']).resolve()
        if not plan_path.is_relative_to(project_root) or not queue_path.is_relative_to(project_root):
            raise ValueError('Owned plan and queue must stay in their project directory')
    jobs = queue.get("jobs", [])
    by_scene = {}
    for job in jobs:
        scene = job.get("scene_id")
        if scene in by_scene:
            raise ValueError("Duplicate scene in queue: " + str(scene))
        by_scene[scene] = job
    for shot in plan["shots"]:
        job = by_scene.get(shot["id"])
        if not job or job.get("status") != "downloaded":
            raise ValueError("No real downloaded job for " + shot["id"])
        if shot.get("prompt_sha256") != job.get("prompt_sha256"):
            raise ValueError("Prompt mismatch: " + shot["id"])
        if hashlib.sha256(job["prompt"].encode("utf-8")).hexdigest() != job.get("prompt_sha256"):
            raise ValueError("Queue prompt was edited: " + shot["id"])
        data = job.get("output", {})
        p = resolve(queue_path.parent, data.get("file_path", ""))
        if identity.get('project_instance_id') and not p.is_relative_to(project_root):
            raise ValueError('Downloaded asset outside owned project; copy intentional shared assets before binding: ' + shot['id'])
        if not p.is_file() or sha256(p) != data.get("sha256"):
            raise ValueError("Missing/changed download: " + shot["id"])
        for ref in job.get("reference_files", []):
            ref_path = resolve(queue_path.parent, ref["file_path"])
            expected = ref.get("sha256") or ref.get("file_sha256")
            if not expected or sha256(ref_path) != expected:
                raise ValueError("Changed reference: " + shot["id"])
        old = shot.get("image_sha256")
        shot["image"], shot["image_sha256"] = str(p), data["sha256"]
        if old != data["sha256"]:
            shot["protection"] = {"reviewed": False, "image_sha256": data["sha256"], "regions": []}
    output = Path(args.output).resolve()
    if identity.get('project_instance_id') and not output.is_relative_to(project_root):
        raise ValueError('Bound output cannot overwrite another project')
    if output.exists() and not args.replace:
        raise ValueError("Bound plan exists; use a new revision or explicit --replace")
    write_json(output, plan)
    return {"status": "images_bound", "scenes": len(plan["shots"]), "output": str(output), "mg_protection_review_required": bool(plan.get("mg"))}


def bind_science(args):
    """Bind an explicitly selected ledger revision; no cue/media/queue changes or approval."""
    plan_path, output = Path(args.plan).resolve(), Path(args.output).resolve()
    plan = read_json(plan_path)
    if output.exists() and not args.replace:
        raise ValueError('Science-bound plan exists; choose a new revision')
    ledger_path = resolve(plan_path.parent, args.ledger)
    if not isinstance(read_json(ledger_path), dict):
        raise ValueError('Science ledger file must contain an object')
    freeze_science_ledger(plan, str(ledger_path), ledger_path.parent, require_file=True)
    write_json(output, plan)
    return {'status': 'science_revision_bound', 'output': str(output), 'ledger_sha256': plan['science_source_ledger_sha256'], 'scientific_truth_verified_by_tool': False, 'review_state_approved_by_tool': False, 'image_generation_executed': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("compile")
    p.add_argument("--project", required=True)
    p.add_argument("--storyboard", default="storyboard.json")
    p.add_argument("--base-plan", default="plan.json")
    p.add_argument("--output", default="production_plan.json")
    p.add_argument("--group-size", type=int, default=8)
    p.add_argument("--replace", action="store_true")
    p = sub.add_parser('bind-science', help='Freeze an explicitly reviewed ledger revision; does not approve facts or touch the image queue')
    p.add_argument('--plan', required=True)
    p.add_argument('--ledger', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--replace', action='store_true')
    p = sub.add_parser("bind-images")
    p.add_argument("--plan", required=True)
    p.add_argument("--queue", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "compile" and args.group_size < 1:
            raise ValueError("group-size must be positive")
        result = compile_plan(args) if args.command == 'compile' else bind_science(args) if args.command == 'bind-science' else bind_images(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        parser.exit(2, "ERROR: %s\n" % exc)


if __name__ == "__main__":
    main()
