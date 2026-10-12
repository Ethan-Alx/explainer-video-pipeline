"""One native writer, conservative input gate and independent saved-JSON checks.

Ported from the project's accepted V2/V3/V4 mechanisms without personal paths,
counts, media or per-shot exceptions. Pillow/MediaInfo/native imports are lazy.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from fractions import Fraction
from pathlib import Path

from coordination import resource_lock

from png_layout import (registered_canvas, prepare_png_canvas,
                          png_layout_geometry, validate_registration_groups)

ROOT = Path(__file__).resolve().parents[1]
MOTIONS = {'static', 'zoom_in', 'zoom_out', 'pan_left', 'pan_right', 'pan_up', 'pan_down'}


def configure(deps=None):
    if deps:
        deps = Path(deps).resolve()
        require(deps.is_dir(), f'Dependency directory not found: {deps}')
        sys.path.insert(0, str(deps))
    sys.path.insert(0, str(ROOT / 'vendor'))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def resolve(base, value):
    p = Path(value)
    return (p if p.is_absolute() else Path(base) / p).resolve()


def json_sha(value):
    """Canonical UTF-8 JSON digest for an embedded ledger; no factual approval."""
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def science_ledger_inputs(plan, base):
    """Read only declared ledger/local sources, resolving file-ledger refs by its directory."""
    value = plan.get('science_source_ledger')
    if value is None:
        return None
    if isinstance(value, str):
        original = resolve(base, value)
        require(original.is_file(), f'Science ledger file missing: {original}')
        ledger, source_base, digest, digest_kind = read(original), original.parent, sha(original), 'file-bytes'
    else:
        require(isinstance(value, dict), 'Science ledger must be an object or a file path')
        original = None
        ledger, source_base, digest, digest_kind = copy.deepcopy(value), Path(base), json_sha(value), 'canonical-json'
    require(isinstance(ledger, dict), 'Science ledger file must contain an object')
    expected = plan.get('science_source_ledger_sha256')
    if expected:
        require(plan.get('science_source_ledger_sha256_kind', digest_kind) == digest_kind and expected == digest, 'Locked science ledger changed')
    if plan.get('science_source_ledger_original_file'):
        receipt_file = resolve(base, plan['science_source_ledger_original_file'])
        require(receipt_file.is_file() and sha(receipt_file) == plan.get('science_source_ledger_original_sha256'), 'Original science ledger evidence changed')
    rows = ledger.get('sources', [])
    require(isinstance(rows, list) and all(isinstance(r, dict) for r in rows), 'Science ledger sources must be a list of objects')
    files = []
    for index, row in enumerate(rows):
        if not row.get('file'):
            continue
        require(isinstance(row['file'], str), 'Science source file must be a local path string')
        path = resolve(source_base, row['file'])
        require(path.is_file(), f'Science source file missing: {path}')
        source_sha = sha(path)
        require(not row.get('sha256') or row['sha256'] == source_sha, f'Science source file SHA mismatch: {row.get("id", index)}')
        files.append((index, path, source_sha))
    return {'ledger': ledger, 'original': original, 'digest': digest, 'digest_kind': digest_kind, 'files': files}


def stage_science_ledger(plan, base, snapshot, output):
    """Preserve declared review states and package provenance; never certify scientific truth."""
    info = science_ledger_inputs(plan, base)
    if info is None:
        return []
    science_dir = output/'media'/'sources'/'science'
    science_dir.mkdir(parents=True, exist_ok=True)
    ledger = copy.deepcopy(info['ledger'])
    evidence = []
    for index, src, digest in info['files']:
        dest = science_dir/'sources'/(digest+src.suffix.lower())
        if not dest.exists():
            stage_file(src, dest)
        relative_base = science_dir if info['original'] else output
        ledger['sources'][index]['file'] = dest.relative_to(relative_base).as_posix()
        ledger['sources'][index]['sha256'] = digest
        evidence.append({'kind': 'science_source_file', 'source_id': ledger['sources'][index].get('id'), 'asset_path': str(dest), 'asset_sha256': digest})
    if info['original']:
        original_copy = stage_file(info['original'], science_dir/'ledger.original.json')
        portable = science_dir/'ledger.json'
        write(portable, ledger)
        snapshot['science_source_ledger'] = portable.relative_to(output).as_posix()
        snapshot['science_source_ledger_sha256'] = sha(portable)
        snapshot['science_source_ledger_sha256_kind'] = 'file-bytes'
        snapshot['science_source_ledger_original_file'] = original_copy.relative_to(output).as_posix()
    else:
        snapshot['science_source_ledger'] = ledger
        snapshot['science_source_ledger_sha256'] = json_sha(ledger)
        snapshot['science_source_ledger_sha256_kind'] = 'canonical-json'
    snapshot['science_source_ledger_original_sha256'] = info['digest']
    snapshot['science_source_ledger_original_sha256_kind'] = info['digest_kind']
    evidence.append({'kind': 'science_ledger', 'source_sha256': info['digest'], 'portable_sha256': snapshot['science_source_ledger_sha256'], 'review_states_changed': False, 'scientific_truth_verified': False})
    return evidence


def safe_name(name):
    require(isinstance(name, str) and name.strip() == name and name not in ('', '.', '..'), 'Invalid draft name')
    require(not re.search(r'[<>:"/\\|?*\x00-\x1f]', name) and not name.endswith(('.', ' ')), 'Invalid draft name characters')
    require(name.split('.')[0].upper() not in {'CON', 'PRN', 'AUX', 'NUL', *('COM' + str(i) for i in range(1, 10)), *('LPT' + str(i) for i in range(1, 10))}, 'Reserved Windows draft name')
    require(len(name) <= 120, 'Draft name too long')
    return name


def image_lib():
    try:
        from PIL import Image, ImageOps
        return Image, ImageOps
    except ImportError as exc:
        raise ImportError(f'Pillow unavailable; install {ROOT / "requirements.txt"}') from exc


def native():
    try:
        return importlib.import_module('pyJianYingDraft')
    except ImportError as exc:
        raise ImportError(f'Native draft dependency unavailable ({exc}); install {ROOT / "requirements.txt"}') from exc


def media_duration(path):
    path = Path(path)
    require(path.is_file(), f'Audio not found: {path}')
    if path.suffix.lower() == '.wav':
        with wave.open(str(path), 'rb') as wav:
            require(wav.getcomptype() == 'NONE', 'Only unchanged PCM WAV duration can be proved from frames')
            exact = Fraction(wav.getnframes() * 1000000, wav.getframerate())
            require(exact.denominator == 1, 'PCM duration is not an integer microsecond; use source MP3 or exact-frame WAV')
            return exact.numerator, {'method': 'PCM frames', 'sample_rate': wav.getframerate(), 'frames': wav.getnframes()}
    try:
        from pymediainfo import MediaInfo
    except ImportError as exc:
        raise ImportError(f'pymediainfo unavailable; install {ROOT / "requirements.txt"}') from exc
    require(MediaInfo.can_parse(), 'MediaInfo native parser is unavailable; doctor must pass before build')
    info = MediaInfo.parse(str(path))
    require(info.audio_tracks, f'No audio track in {path}')
    track = info.audio_tracks[0]
    duration = getattr(track, 'duration', None)
    if duration is None and info.general_tracks:
        duration = getattr(info.general_tracks[0], 'duration', None)
    require(duration is not None and float(duration) > 0, f'Cannot prove actual audio duration: {path}')
    return round(float(duration) * 1000), {'method': 'MediaInfo source audio track', 'reported_ms': duration, 'format': getattr(track, 'format', None)}


def video_duration(path):
    from pymediainfo import MediaInfo
    info=MediaInfo.parse(str(path));require(bool(info.video_tracks),f'No real video track: {path}')
    t=info.video_tracks[0];duration=getattr(t,'duration',None)
    if duration is None and info.general_tracks:duration=getattr(info.general_tracks[0],'duration',None)
    require(duration is not None and float(duration)>0,f'No video duration: {path}')
    return round(float(duration)*1000),{'method':'MediaInfo actual video track','duration_ms':duration,'width':getattr(t,'width',None),'height':getattr(t,'height',None),'frame_rate':getattr(t,'frame_rate',None)}


def doctor(require_video_tools=False):
    checks = {}
    try:
        from runtime_paths import find_font, font_diagnostics
        font = find_font()
        checks['subtitle_measurement_font'] = {'ok': True, 'path': str(font), **font_diagnostics(font)}
    except (ValueError,OSError) as exc:
        checks['subtitle_measurement_font'] = {'ok': False, 'error': str(exc)}
    from runtime_paths import ffmpeg_diagnostics
    checks['FFmpeg']=ffmpeg_diagnostics(required=require_video_tools)
    for package in ('PIL', 'pymediainfo', 'imageio', 'numpy'):
        try:
            mod = importlib.import_module(package)
            checks[package] = {'ok': True, 'version': getattr(mod, '__version__', None)}
        except ImportError as exc:
            checks[package] = {'ok': False, 'error': str(exc)}
    try:
        from pymediainfo import MediaInfo
        checks['MediaInfo_parser'] = {'ok': bool(MediaInfo.can_parse())}
    except (ImportError, OSError) as exc:
        checks['MediaInfo_parser'] = {'ok': False, 'error': str(exc)}
    try:
        d = native()
        checks['native_library'] = {'ok': all(hasattr(d, key) for key in ('DraftFolder', 'TrackSpec', 'TextSegment', 'KeyframeProperty')), 'path': str(Path(d.__file__).resolve()), 'version': 'vendored 0.3.0'}
    except (ImportError, OSError) as exc:
        checks['native_library'] = {'ok': False, 'error': str(exc)}
    return {'ok': all(c['ok'] for c in checks.values()), 'python': sys.version.split()[0], 'executable': sys.executable, 'checks': checks, 'install_dependencies': f'python -m pip install -r "{ROOT / "requirements.txt"}"', 'gui_used': False}


def parse_srt(path):
    text = Path(path).read_text(encoding='utf-8-sig').replace('\r\n', '\n').replace('\r', '\n').strip()
    cues = []
    stamp = re.compile(r'^(\d+):(\d{2}):(\d{2})[,.](\d{1,3})$')
    def micros(value):
        match = stamp.match(value.strip())
        require(match is not None, f'Invalid SRT timestamp: {value}')
        h, m, s = [int(x) for x in match.groups()[:3]]
        require(m < 60 and s < 60, f'Invalid SRT timestamp: {value}')
        return ((h * 60 + m) * 60 + s) * 1000000 + int(match.group(4).ljust(3, '0')) * 1000
    for block in re.split(r'\n\s*\n', text):
        lines = block.split('\n')
        require(len(lines) >= 3 and lines[0].strip().isdigit(), 'SRT must have cue number, time and text')
        parts = lines[1].split('-->')
        require(len(parts) == 2, f'Invalid SRT cue: {lines[0]}')
        start, end = [micros(p) for p in parts]
        content = '\n'.join(lines[2:])
        require(start >= 0 and end > start and content.strip(), f'Invalid SRT range/content: {lines[0]}')
        cues.append({'number': int(lines[0]), 'start_us': start, 'end_us': end, 'text': content})
    require(cues and len({c['number'] for c in cues}) == len(cues), 'SRT is empty or has duplicate cue numbers')
    require(all(cues[i]['start_us'] <= cues[i + 1]['start_us'] for i in range(len(cues) - 1)), 'SRT cue order must be chronological')
    return cues


def init_project(project, name, audio, srt, manuscript=None, width=1920, height=1080, fps=30, render_subtitles=True):
    project, audio, srt = Path(project).resolve(), Path(audio).resolve(), Path(srt).resolve()
    safe_name(name)
    require(width > 0 and height > 0 and 1 <= fps <= 120, 'Invalid canvas')
    require(not project.exists(), f'Project exists; refuse overwrite: {project}')
    duration, proof = media_duration(audio)
    cues = parse_srt(srt)
    require(max(c['end_us'] for c in cues) <= duration, 'SRT extends beyond source audio; verify the matching MP3/SRT')
    inputs = {'audio': audio, 'srt': srt}
    if manuscript:
        inputs['manuscript'] = Path(manuscript).resolve()
    for path in inputs.values():
        require(path.is_file(), f'Source missing: {path}')
    project.mkdir(parents=True, exist_ok=False)
    sources = {}
    for kind, src in inputs.items():
        dest = project / 'inputs' / (kind + src.suffix.lower())
        dest.parent.mkdir(exist_ok=True)
        shutil.copy2(src, dest)
        sources[kind] = dest.relative_to(project).as_posix()
        sources[kind + '_sha256'] = sha(src)
        require(sha(dest) == sources[kind + '_sha256'], 'Source copy changed')
    for folder in ('images', 'mg', 'sfx', 'review'):
        (project / folder).mkdir()
    plan = {'schema_version': 1, 'render_subtitles': bool(render_subtitles), 'name': name, 'canvas': {'width': width, 'height': height, 'fps': fps, 'duration_us': duration}, 'sources': sources, 'shots': [], 'mg': [], 'sfx': [], 'subtitle_style': {'size': 4.4 * height / 1080, 'y': -0.78, 'max_line_width': 0.86}}
    write(project / 'plan.json', plan)
    write(project / 'sources.json', {'sources': sources, 'duration_us': duration, 'duration_proof': proof, 'cues': cues, 'audio_content_modified': False, 'subtitle_content_modified': False})
    return {'ok': True, 'project': str(project), 'plan': str(project / 'plan.json'), 'duration_us': duration, 'subtitle_count': len(cues), 'next': 'Initialize project memory with scripts/project_memory.py init --project <project>; then fill approved real images and reviewed MG/SFX before validate/build.'}


def int_time(value, field):
    require(isinstance(value, int) and not isinstance(value, bool), f'{field} must be integer microseconds')
    return value


def box(value, w, h, label):
    require(isinstance(value, list) and len(value) == 4 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in value), f'Invalid bbox: {label}')
    x0, y0, x1, y1 = value
    require(0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, f'BBox outside canvas: {label}')
    return value


def intersects(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def overlaps_time(a, b):
    return a['start_us'] < b['end_us'] and b['start_us'] < a['end_us']


def camera_points(shot):
    c = shot.get('camera', {})
    if c.get('keyframes'):
        return copy.deepcopy(c['keyframes'])
    motion, amount = c.get('motion', 'static'), c.get('amount', 0.04)
    fx, fy = c.get('focus', [0.5, 0.5])
    dur = shot['end_us'] - shot['start_us']
    points = []
    for i in range(9):
        p = i / 8
        t = p * p * (3 - 2 * p)
        scale = 1 + amount * (t if motion == 'zoom_in' else 1 - t if motion == 'zoom_out' else 0 if motion == 'static' else 1)
        x, y = (0.5 - fx) * 2 * (scale - 1), (fy - 0.5) * 2 * (scale - 1)
        if motion.startswith('pan_'):
            x = amount * (2 * t - 1) * (1 if motion == 'pan_right' else -1 if motion == 'pan_left' else 0)
            y = amount * (2 * t - 1) * (1 if motion == 'pan_up' else -1 if motion == 'pan_down' else 0)
        points.append({'at_us': round(dur * p), 'scale': scale, 'x': x, 'y': y})
    return points


def camera_box(b, point, w, h):
    scale, dx, dy = point['scale'], point['x'] * w / 2, -point['y'] * h / 2
    return [(b[0] - w / 2) * scale + w / 2 + dx, (b[1] - h / 2) * scale + h / 2 + dy, (b[2] - w / 2) * scale + w / 2 + dx, (b[3] - h / 2) * scale + h / 2 + dy]


def mg_envelope(e):
    b = list(e.get('_v7_actual_ink_bbox_px', e['bbox_px']))
    anim = e.get('animation', {})
    if anim.get('keyframes'):
        x0,y0,x1,y1=b;layout=e['bbox_px'];cx,cy=(layout[0]+layout[2])/2,(layout[1]+layout[3])/2
        poses=[]
        for p in anim['keyframes']:
            scale=p.get('scale',1);dx,dy=p.get('dx_px',0),p.get('dy_px',0)
            poses.append([cx+(x0-cx)*scale+dx,cy+(y0-cy)*scale+dy,cx+(x1-cx)*scale+dx,cy+(y1-cy)*scale+dy])
        return [min(x[0] for x in poses),min(x[1] for x in poses),max(x[2] for x in poses),max(x[3] for x in poses)]
    dy = abs(anim.get('move_y_px', 8)) + abs(anim.get('loop', {}).get('amplitude_px', 0))
    b[1] -= dy
    b[3] += dy
    return b


def load_plan(path):
    path = Path(path).resolve()
    plan = read(path)
    for s in plan.get('shots', []):
        if 'id' not in s and 'scene_id' in s:
            s['id'] = s['scene_id']
    return path, plan


def mechanism_pose(layer, absolute_at_us):
    points = layer.get('animation', {}).get('keyframes', [])
    defaults = {'scale': 1, 'dx_px': 0, 'dy_px': 0, 'alpha': 1}
    if not points:
        return defaults
    t = max(0, min(absolute_at_us-layer['start_us'], layer['end_us']-layer['start_us']))
    for a, b in zip(points, points[1:]):
        if a['at_us'] <= t <= b['at_us']:
            f = (t-a['at_us'])/(b['at_us']-a['at_us'])
            return {k: a.get(k, v)+(b.get(k, v)-a.get(k, v))*f for k, v in defaults.items()}
    return {k: points[-1].get(k, v) for k, v in defaults.items()}


def actual_shape_signature(path):
    """Compare silhouettes/dark contour geometry, excluding fill colour and text."""
    from PIL import Image
    import numpy as np
    with Image.open(path) as source:
        pixels = np.asarray(source.convert('RGBA'))
        alpha = pixels[:, :, 3] > 16
        ink = alpha & (np.max(pixels[:, :, :3], axis=2) < 95)
        payload = json.dumps([source.width, source.height]).encode()+np.packbits(alpha).tobytes()+np.packbits(ink).tobytes()
        return hashlib.sha256(payload).hexdigest()


def mechanism_registry_inputs(plan, base):
    """Verify frozen visual evidence against actual PNGs and native layer timing."""
    filename = plan.get('mechanism_registry_file')
    if not filename:
        return None
    path = resolve(base, filename)
    require(path.is_file() and sha(path) == plan.get('mechanism_registry_sha256'), 'Mechanism registry missing or changed')
    registry = read(path)
    groups = {}; layers = {e['id']: e for e in plan.get('mg', [])}; cache = {}; samples = 0
    for group in registry.get('mechanisms', []):
        gid = group['mechanism_id']
        require(gid not in groups and group.get('reviewed') is True, 'Mechanism group duplicate or not actually reviewed')
        actual = group.get('actual_layers', [])
        require(actual, f'Mechanism {gid} has no independent actual PNG objects')
        by_id = {}
        for item in actual:
            e = layers.get(item['element_id'])
            require(e is not None and e.get('type') == 'png', f'Mechanism object missing from plan: {item["element_id"]}')
            asset = resolve(base, e['asset'])
            if str(asset) not in cache:
                cache[str(asset)] = (sha(asset), actual_shape_signature(asset))
            digest, shape = cache[str(asset)]
            require(digest == item['asset_sha256'] and shape == item['shape_signature'], 'Mechanism actual PNG bytes or shape differ from reviewed object')
            require(all(e.get(k) == item.get(k) for k in ('bbox_px', 'layer', 'start_us', 'end_us', 'animation')), 'Mechanism object pose/timing differs from actual evidence')
            if registered_canvas(e):
                require(all(e.get(k) == item.get(k) for k in ('content_fit', 'preserve_registered_canvas', 'registration_group')),
                    'Registered mechanism canvas mode differs from actual evidence')
                geometry = png_layout_geometry(asset, e, (plan['canvas']['width'], plan['canvas']['height']))
                require(item.get('visible_bbox_px') == geometry['visible_bbox_px'],
                    'Registered mechanism visible ink differs; layout bbox is not its visible geometry')
            require(item['element_id'] not in by_id, 'Duplicate mechanism object evidence')
            by_id[item['element_id']] = item
        for sample in group.get('state_evidence_samples', []):
            require(sample.get('reviewed') is True, 'Unreviewed mechanism state')
            t = sample['absolute_at_us']; objects = []
            for item in actual:
                if not item['start_us'] <= t < item['end_us']:
                    continue
                pose = mechanism_pose(item, t)
                if pose['alpha'] <= .05:
                    continue
                obj = {'element_id': item['element_id'], 'asset_sha256': item['asset_sha256'],
                    'shape_signature': item['shape_signature'], 'bbox_px': item['bbox_px'], 'layer': item['layer'],
                    **{k: round(pose[k], 6) for k in ('scale', 'dx_px', 'dy_px')}}
                if registered_canvas(layers[item['element_id']]):
                    obj['visible_bbox_px'] = item['visible_bbox_px']
                objects.append(obj)
            require(objects == sample.get('objects') and objects, 'Mechanism state does not match actual visible PNG geometry')
            geometry = [{k:v for k,v in o.items() if k not in ('element_id', 'asset_sha256')} for o in objects]
            require(json_sha(sorted(geometry, key=json_sha)) == sample.get('geometry_signature'), 'Mechanism geometry evidence signature differs')
            if plan.get('mechanism_evidence_directory'):
                proxy = resolve(base, plan['mechanism_evidence_directory']) / Path(sample['proxy_file']).name
            else:
                proxy = Path(sample['proxy_file'])
            require(proxy.is_file() and sha(proxy) == sample['proxy_sha256'], 'Actual reviewed mechanism proxy missing or changed')
            samples += 1
        require(group.get('state_evidence_samples'), f'Mechanism {gid} has no actual states')
        groups[gid] = group
    require(groups, 'Empty mechanism registry')
    return {'path': path, 'registry': registry, 'groups': groups, 'actual_png_files': len(cache), 'actual_state_samples': samples}


def validate_visual_repetition(plan, base):
    """Camera/text cannot turn any identical still reuse into new visual information."""
    from reuse_review import validate_reuse_review
    previous=None;cache={};checked=0;waivers=[];seen_stills={};global_waivers=[];semantic_reviews=[]
    mechanisms = mechanism_registry_inputs(plan, base)
    for shot in plan.get('shots',[]):
        path=resolve(base,shot.get('image',''))
        if str(path) not in cache:cache[str(path)]=sha(path)
        signature=cache[str(path)]
        semantic_review=None
        if shot.get('media_type','image') == 'image':
            earlier = seen_stills.get(signature, [])
            if earlier and shot.get('reuse_review') is not None:
                semantic_review=validate_reuse_review(shot,earlier,plan,base)
                semantic_reviews.append(semantic_review)
            if earlier and not semantic_review:
                current_group = (mechanisms or {}).get('groups', {}).get(shot.get('main_mechanism_plate'))
                require(current_group and signature == current_group.get('main_asset', {}).get('sha256'),
                    f'Repeated still needs actual mechanism states or a scoped, evidenced reuse_review: {earlier[0]["id"]} -> {shot["id"]}')
                require(current_group['start_us'] <= shot['start_us'] < shot['end_us'] <= current_group['end_us'],
                    'Repeated mechanism background is outside its reviewed actual state window')
                prior_geometry = set()
                for prior in earlier:
                    old_group = (mechanisms or {}).get('groups', {}).get(prior.get('main_mechanism_plate'))
                    require(old_group and signature == old_group.get('main_asset', {}).get('sha256'),
                        'Earlier repeated still is not a reviewed mechanism background')
                    require(old_group['start_us'] <= prior['start_us'] < prior['end_us'] <= old_group['end_us'],
                        'Earlier repeated mechanism background is outside its reviewed state window')
                    prior_geometry.update(s['geometry_signature'] for s in old_group['state_evidence_samples']
                        if prior['start_us'] <= s['absolute_at_us'] < prior['end_us'])
                current_geometry = {s['geometry_signature'] for s in current_group['state_evidence_samples']
                    if shot['start_us'] <= s['absolute_at_us'] < shot['end_us']}
                require(prior_geometry and current_geometry-prior_geometry,
                    f'Repeated mechanism plate has no new actual object geometry: {shot["id"]}')
                global_waivers.append({'shot_id': shot['id'], 'earlier_shot_ids': [s['id'] for s in earlier],
                    'kind': 'reviewed_new_independent_actual_geometry',
                    'new_geometry_signatures': sorted(current_geometry-prior_geometry)})
            seen_stills.setdefault(signature, []).append(shot)
        if previous and previous['media_type']=='image' and shot.get('media_type','image')=='image':
            checked+=1
            if previous['sha256'] == signature and not semantic_review:
                a = (mechanisms or {}).get('groups', {}).get(previous['shot'].get('main_mechanism_plate'))
                b = (mechanisms or {}).get('groups', {}).get(shot.get('main_mechanism_plate'))
                require(a and b, f'V4 adjacent repeated still has no actual mechanism: {previous["id"]} -> {shot["id"]}; camera/text/colour cannot waive this')
                for group, current in ((a, previous['shot']), (b, shot)):
                    require(signature == group.get('main_asset', {}).get('sha256'), 'Repeated still is not the actual reviewed mechanism background')
                    require(group['start_us'] <= current['start_us'] < current['end_us'] <= group['end_us'], 'Mechanism plate shot is outside actual reviewed mechanism window')
                sample_a = [s for s in a['state_evidence_samples'] if previous['shot']['start_us'] <= s['absolute_at_us'] < previous['shot']['end_us']]
                sample_b = [s for s in b['state_evidence_samples'] if shot['start_us'] <= s['absolute_at_us'] < shot['end_us']]
                require(sample_a and sample_b and sample_a[-1]['geometry_signature'] != sample_b[0]['geometry_signature'], 'Same plate repeats without an actual object/position/relationship change at the cut')
                waivers.append({'before': previous['id'], 'after': shot['id'], 'kind': 'independent_actual_object_geometry',
                    'before_state_sha256': sample_a[-1]['geometry_signature'], 'after_state_sha256': sample_b[0]['geometry_signature']})
        previous={'id':shot['id'],'sha256':signature,'media_type':shot.get('media_type','image'),'shot':shot}
    return {'adjacent_still_pairs_checked':checked,'same_sha_still_cut_waivers':len(waivers),'actual_mechanism_waivers':waivers,
        'all_still_uses_checked':sum(len(rows) for rows in seen_stills.values()),'unique_still_sha256':len(seen_stills),
        'nonadjacent_and_adjacent_reuse_guard':True,'global_actual_mechanism_waivers':global_waivers,
        'reviewed_semantic_reuses':semantic_reviews,
        'reviewed_mechanism_groups':len((mechanisms or {}).get('groups', {})), 'actual_state_samples': (mechanisms or {}).get('actual_state_samples', 0)}


def action_sync_time_us(effect, shots_by_id, base, mg_by_id=None):
    sync=effect.get('action_sync')
    if not sync:return None
    require(sync.get('kind') in ('video_action','mg_event'),'Only measured video_action or actual mg_event anchors supported')
    require(sync.get('reviewed') is True and sync.get('evidence_file'),'Action attack needs actual reviewed-frame/authored-MG evidence')
    evidence=resolve(base,sync['evidence_file'])
    require(evidence.is_file() and sync.get('evidence_sha256')==sha(evidence),'Action attack evidence file missing or changed')
    if sync['kind']=='video_action':
        shot=shots_by_id.get(sync.get('shot_id'))
        require(shot is not None and shot.get('media_type')=='video','Action SFX needs its actual video shot')
        require(sync.get('video_sha256')==sha(resolve(base,shot['image'])),'Action SFX references wrong video bytes')
        attack=int_time(sync.get('source_attack_us'),'action_sync.source_attack_us')
        source_start=shot.get('source_start_us',0);speed=shot.get('playback_speed',1)
        source_end=source_start+round((shot['end_us']-shot['start_us'])*speed)
        require(source_start<=attack<source_end,'Measured source attack is outside selected actual video range')
        target_attack=shot['start_us']+round((attack-source_start)/speed)
        target_info={'video_shot_id':shot['id'],'source_attack_us':attack}
    else:
        element=(mg_by_id or {}).get(sync.get('element_id'))
        require(element is not None and element.get('type')=='png','MG event requires its actual independent PNG layer')
        require(sync.get('mg_asset_sha256')==sha(resolve(base,element['asset'])),'MG event references wrong PNG bytes')
        attack=int_time(sync.get('keyframe_at_us'),'action_sync.keyframe_at_us')
        require(0<=attack<element['end_us']-element['start_us'],'MG event is outside the actual layer lifetime')
        points=element.get('animation',{}).get('keyframes',[])
        kind=sync.get('event_kind')
        if kind=='entry':
            require(attack==0,'MG entry means this independent layer first becomes visible at its start')
            alpha=points[0].get('alpha',1) if points else 1
            require(alpha>0,'Invisible alpha-zero layer has no visible entry event')
        elif kind=='keyframe_change':
            current=next((i for i,p in enumerate(points) if p['at_us']==attack),None)
            require(current is not None and current>0,'MG event needs its actual noninitial keyframe time')
            a,b=points[current-1],points[current]
            require(any(abs(a.get(k,default)-b.get(k,default))>1e-10 for k,default in [('scale',1),('dx_px',0),('dy_px',0),('alpha',1)]),'MG event keyframe has no actual changed scalar value')
        else:raise ValueError('MG event kind must be entry or keyframe_change; contact/rupture meaning requires separate actual evidence')
        target_attack=element['start_us']+attack
        target_info={'mg_element_id':element['id'],'keyframe_at_us':attack,'event_kind':kind}
    audio_attack=int_time(sync.get('audio_attack_us'),'action_sync.audio_attack_us')
    require(effect.get('source_start_us',0)<=audio_attack<effect.get('source_start_us',0)+effect['duration_us'],'Audio attack is outside selected WAV source range')
    target_audio_attack=effect['start_us']+audio_attack-effect.get('source_start_us',0)
    tolerance=sync.get('tolerance_us',1000)
    require(isinstance(tolerance,int) and not isinstance(tolerance,bool) and 0<=tolerance<=50000,'Action sync tolerance may be at most 50ms')
    require(abs(target_attack-target_audio_attack)<=tolerance,f'SFX action attack misaligned: {effect["id"]}; video={target_attack}us audio={target_audio_attack}us')
    return {**target_info,'visual_target_attack_us':target_attack,'audio_target_attack_us':target_audio_attack,'difference_us':target_audio_attack-target_attack}


def validate_plan(path):
    path, plan = load_plan(path)
    from motion_compatibility import require_build_approval, require_new_artwork
    require_build_approval(plan)
    artwork_guard = require_new_artwork(plan, path.parent)
    require(plan.get('schema_version') == 1, 'Unsupported plan schema_version')
    safe_name(plan.get('name', ''))
    c = plan.get('canvas', {})
    w, h, fps, duration = (c.get(k) for k in ('width', 'height', 'fps', 'duration_us'))
    require(all(isinstance(x, int) and not isinstance(x, bool) for x in (w, h, fps, duration)) and w > 0 and h > 0 and 1 <= fps <= 120 and duration > 0, 'Canvas needs positive integer width/height/fps/duration_us')
    base, sources = path.parent, plan.get('sources', {})
    # Portable font paths are relative to the saved plan, not the process cwd.
    for font_element in plan.get('mg', []):
        if font_element.get('font_file'):
            font_element['font_file'] = str(resolve(base, font_element['font_file']))
    science_ledger_inputs(plan, base)  # Path/hash checks only; source truth is a separate review.
    source_paths = {}
    for key in ('audio', 'srt', 'manuscript'):
        if key == 'manuscript' and not sources.get(key):
            continue
        require(sources.get(key) and sources.get(key + '_sha256'), f'Missing locked source: {key}')
        p = resolve(base, sources[key])
        require(p.is_file() and sha(p) == sources[key + '_sha256'], f'Locked {key} changed or missing: {p}')
        source_paths[key] = p
    actual, proof = media_duration(source_paths['audio'])
    require(duration == actual, f'Canvas duration {duration} must equal unchanged audio duration {actual}; no stretching or guessed extension')
    cues = parse_srt(source_paths['srt'])
    require(max(c['end_us'] for c in cues) <= duration, 'SRT extends beyond source audio')
    shots, mg, sfx = plan.get('shots', []), plan.get('mg', []), plan.get('sfx', [])
    require(isinstance(shots, list) and shots, 'No real image shots; fill the production plan before build')
    require(isinstance(mg, list) and isinstance(sfx, list), 'mg/sfx must be lists')
    ids = [s.get('id') for s in shots] + [e.get('id') for e in mg] + [e.get('id') for e in sfx]
    require(all(isinstance(x, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,64}', x) for x in ids) and len(ids) == len(set(ids)), 'IDs must be unique safe letters/numbers/underscore/hyphen')
    Image, _ = image_lib()
    previous = 0
    for i, s in enumerate(shots):
        a, b = int_time(s.get('start_us'), 'shot.start_us'), int_time(s.get('end_us'), 'shot.end_us')
        require(a == previous and a < b <= duration, f'Shots must continuously cover the entire audio, gap/overlap at {s["id"]}')
        previous = b
        p = resolve(base, s.get('image', ''))
        require(p.is_file(), f'Main image missing: {s["id"]}: {p}')
        if s.get('media_type','image')=='video':
            source_dur,_=video_duration(p)
            speed=s.get('playback_speed',1)
            require(isinstance(speed,(float,int)) and .25<=speed<=4,'Video speed invalid')
            source_at=s.get('source_start_us',0)
            require(0<=source_at and source_at+round((b-a)*speed)<=source_dur+1000,f'Video source does not cover target duration: {s["id"]}')
        else:
            with Image.open(p) as im: im.verify()
        if s.get('image_sha256'):
            require(sha(p) == s['image_sha256'], f'Main image changed: {s["id"]}')
        cam = s.get('camera', {})
        require(cam.get('motion', 'static') in MOTIONS, f'Unsupported camera: {s["id"]}')
        require(isinstance(cam.get('amount', .04), (int, float)) and 0 <= cam.get('amount', .04) <= .4, 'Camera amount must be 0 to 0.4')
        focus = cam.get('focus', [.5, .5])
        require(isinstance(focus, list) and len(focus) == 2 and all(isinstance(x, (int, float)) and 0 <= x <= 1 for x in focus), 'Camera focus must be normalized xy')
        if cam.get('keyframes'):
            points=cam['keyframes'];times=[p['at_us'] for p in points]
            require(times==sorted(set(times)) and times[0]==0 and times[-1]==b-a,'Camera keyframes need exact t0 and end')
            # Explicit full-canvas crops can start at 2x; modest relative movement
            # on such a reviewed detail is valid native scalar motion, not a 100% zoom.
            require(all(all(isinstance(p[k],(int,float)) and math.isfinite(p[k]) for k in ('scale','x','y')) and .75<=p['scale']<=2.5 for p in points),'Bad camera native absolute values (scale must be 0.75 to 2.5)')
        trans = s.get('incoming_transition', {'type': 'cut', 'duration_us': 0})
        typ, length = trans.get('type', 'cut'), int_time(trans.get('duration_us', 0), 'transition.duration_us')
        require(typ in ('cut', 'dissolve', 'dip_black', 'slide_reveal', 'cover_wipe'), 'Unsupported short transition')
        if typ in ('slide_reveal','cover_wipe'):
            require(trans.get('direction','left') in ('left','right','up','down'), 'Invalid local transition direction')
        if typ == 'slide_reveal':
            require(shots[i-1].get('media_type','image')=='image' and s.get('media_type','image')=='image', 'slide_reveal is a still-image reveal; do not replay a video tail')
        if typ == 'dissolve':
            require(shots[i-1].get('media_type','image')=='image' and s.get('media_type','image')=='image', 'dissolve overlays an outgoing still; no video-tail replay')
        if typ == 'cover_wipe':
            require(bool(re.fullmatch(r'#[0-9a-fA-F]{6}',trans.get('color','#FFD568'))), 'cover_wipe needs a literal RGB hex color')
        require((typ == 'cut' and length == 0) or (i > 0 and 0 < length <= min(600000, (b-a)//3, (shots[i-1]['end_us']-shots[i-1]['start_us'])//3)), f'Invalid/long transition at {s["id"]}')
        for r in s.get('protection', {}).get('regions', []):
            box(r.get('bbox_px'), w, h, f'{s["id"]} protection')
    require(previous == duration, 'Final main image must end at the exact source audio tail')
    registration_check=validate_registration_groups(mg, base, (w, h))
    repetition_check=validate_visual_repetition(plan,base)
    from annotation_geometry import Geometry
    ink_geometry=Geometry(mg,base,(w,h))
    for element in mg:
        if element['type'] in ('png','text'):
            element['_v7_actual_ink_bbox_px']=ink_geometry.ink_box(element['id'])
    for e in mg:
        a, b = int_time(e.get('start_us'), 'MG.start_us'), int_time(e.get('end_us'), 'MG.end_us')
        require(0 <= a < b <= duration, f'MG time outside audio: {e["id"]}')
        require(e.get('type') in ('png', 'text', 'video') and str(e.get('purpose', '')).strip(), 'MG must have type and explanation purpose')
        require(b-a <= 8000000 or str(e.get('long_hold_reason', '')).strip(), f'Long MG needs a semantic reason: {e["id"]}')
        require(isinstance(e.get('layer', 1), int) and 0 <= e.get('layer', 1) <= 99, 'Invalid MG layer')
        box(e.get('bbox_px'), w, h, e['id'])
        box(mg_envelope(e), w, h, e['id']+' motion envelope')
        subtitle_y = plan.get('subtitle_style', {}).get('y', -.82)
        require(isinstance(subtitle_y, (int, float)), 'Invalid subtitle y')
        subtitle_band = min(.84, (1-subtitle_y)/2-.07)
        if plan.get('render_subtitles', True):
            require(mg_envelope(e)[3] < h * subtitle_band, f'MG intersects protected subtitle band: {e["id"]}')
        anim = e.get('animation', {})
        if anim.get('keyframes'):
            times=[p['at_us'] for p in anim['keyframes']]
            require(times==sorted(set(times)) and times[0]==0 and times[-1]==b-a,'MG keyframes must have exact t0/end')
            for point in anim['keyframes']:
                require(all(isinstance(point.get(k,v),(float,int)) and math.isfinite(point.get(k,v)) for k,v in [('scale',1),('dx_px',0),('dy_px',0),('alpha',1)]),'Nonfinite MG keyframe')
                require(.05<=point.get('scale',1)<=4 and 0<=point.get('alpha',1)<=1,'MG alpha or scale invalid')
        for key, default in (('entry_us', 180000), ('exit_us', 140000)):
            require(0 <= int_time(anim.get(key, default), key) <= (b-a)//2, f'MG {key} exceeds half its window: {e["id"]}')
        require(isinstance(anim.get('move_y_px', 8), (int, float)) and math.isfinite(anim.get('move_y_px', 8)), 'Invalid MG move_y_px')
        if e['type'] == 'png':
            p = resolve(base, e.get('asset', ''))
            require(p.is_file(), f'MG PNG missing: {e["id"]}')
            with Image.open(p) as im:
                provenance = e.get('artwork_provenance', {})
                geographic_source = resolve(base, provenance.get('source_file', ''))
                geographic_plate = (
                    provenance.get('asset_kind') == 'public_domain_geographical_diagram'
                    and provenance.get('status') == 'bound_code_native_geography_reviewed'
                    and provenance.get('sha256') == sha(p)
                    and geographic_source.is_file() and sha(geographic_source) == provenance.get('source_sha256')
                    and provenance.get('projection', {}).get('name') == 'Equirectangular lon/lat, cropped latitude range'
                    and provenance.get('license_url') == 'https://www.naturalearthdata.com/about/terms-of-use/'
                    and all(provenance.get('visual_review', {}).get(k) is True for k in ('art','science','alpha')))
                nonempty_alpha = 'A' in im.getbands() and im.getchannel('A').getbbox()
                transparent = nonempty_alpha and im.getchannel('A').getextrema()[0] < 255
                # Cropping a perfectly straight six-pixel callout leaves a solid
                # narrow RGBA rectangle. It is still the reviewed line itself,
                # not an opaque background behind an illustration. Its semantic
                # endpoints and label/target geometry are checked by the annotation
                # audit below; larger, multicolour or unbound opaque art stays invalid.
                callout = e.get('annotation_component', {})
                solid_callout = (
                    nonempty_alpha and callout.get('kind') == 'scientific callout'
                    and callout.get('label_id') in {q['id'] for q in mg if q['type'] == 'text'}
                    and callout.get('target_element_id') in {q['id'] for q in mg}
                    and e.get('content_fit') == 'alpha_bbox_contain'
                    and min(im.size) <= 6 and max(im.size) >= 3 * min(im.size)
                    and len(im.convert('RGBA').getcolors(maxcolors=2) or []) == 1
                    and e.get('asset_sha256') == sha(p))
                require(transparent or geographic_plate or solid_callout,
                        f'MG needs nonempty real alpha transparency: {e["id"]}')
        elif e['type']=='video':
            p=resolve(base,e.get('asset',''));require(p.is_file(),'MG video missing')
            source_dur,_=video_duration(p)
            require(e.get('source_start_us',0)+(b-a)<=source_dur+1000,'MG video too short')
        else:
            require(isinstance(e.get('text'), str) and e['text'].strip(), 'MG text is empty')
            require(0 < e.get('text_size', 6) <= 30 and re.fullmatch(r'#[0-9A-Fa-f]{6}', e.get('text_color', '#243C45')), 'Invalid native MG text style')
            require(not anim.get('loop') and 'alpha' not in anim, 'Native editable text alpha loops are unsupported; use a PNG for looping parts')
            require(all(abs(point.get('alpha',1)-1)<1e-10 for point in anim.get('keyframes',[])), 'Native editable text has no alpha keyframe support; use timed clip/scale/position')
        if anim.get('loop'):
            loop = anim['loop']
            require(loop.get('mode') in ('thought_pulse', 'steam_rise') and int_time(loop.get('period_us'), 'loop.period_us') >= 120000, 'Invalid PNG loop')
            require(isinstance(loop.get('amplitude_px', 0), (int, float)) and 0 <= loop.get('amplitude_px', 0) <= h/8, 'Invalid PNG loop amplitude')
        for s in shots:
            if not overlaps_time(s, e):
                continue
            protection = s.get('protection', {})
            require(protection.get('reviewed') is True and protection.get('image_sha256') == sha(resolve(base, s['image'])), f'MG requires reviewed same-SHA actual media protection: {s["id"]}')
            for r in protection.get('regions', []):
                # Union of piecewise-linear native camera poses conservatively covers each protected region.
                poses = [camera_box(r['bbox_px'], point, w, h) for point in camera_points(s)]
                envelope = [min(p[0] for p in poses), min(p[1] for p in poses), max(p[2] for p in poses), max(p[3] for p in poses)]
                require(not intersects(mg_envelope(e), envelope), f'MG obscures protected subject/action: {e["id"]} / {s["id"]} / {r.get("label", "region")}')
    # A mechanism-group string does not prove that labels are readable. Check
    # real alpha/glyph intersections and explicit semantic associations instead.
    from annotation_validation import require_annotation_contract
    annotation_check=require_annotation_contract(plan,base)
    for e in sfx:
        at = int_time(e.get('start_us'), 'SFX.start_us')
        length = int_time(e.get('duration_us'), 'SFX.duration_us')
        source_at = int_time(e.get('source_start_us', 0), 'SFX.source_start_us')
        require(0 <= at < at + length <= duration and source_at >= 0, f'Invalid SFX timeline: {e["id"]}')
        actual, _ = media_duration(resolve(base, e.get('asset', '')))
        require(source_at + length <= actual, f'SFX exceeds actual source: {e["id"]}')
        require(isinstance(e.get('gain_db', -18), (int, float)) and -80 <= e.get('gain_db', -18) <= 0, 'SFX gain must be -80 to 0 dB')
        require(not e.get('requires_action_sync') or bool(e.get('action_sync')),f'Measured physical/action sound lacks actual action anchor: {e["id"]}')
        action_sync_time_us(e,{s['id']:s for s in shots},base,{g['id']:g for g in mg})
    style = plan.get('subtitle_style', {})
    require(0 < style.get('size', 6) <= 30 and -1 <= style.get('y', -.82) <= -.6 and 0 < style.get('max_line_width', .9) <= 1, 'Invalid subtitle style')
    return {'ok': True, 'scope': 'source/plan/actual alpha/glyph annotation geometry and visual-state/action timing; native rendering still requires real sample', 'plan': str(path), 'duration_us': duration, 'counts': {'shots': len(shots), 'subtitles': len(cues) if plan.get('render_subtitles',True) else 0, 'source_subtitle_cues': len(cues), 'mg': len(mg), 'sfx': len(sfx)}, 'duration_proof': proof,'visual_repetition':repetition_check,'registered_canvas_groups':registration_check,'annotation_checks':annotation_check,'declared_artwork_guard':artwork_guard,'action_sync_count':sum(bool(e.get('action_sync')) for e in sfx)}


def lane_assign(entries, prefix):
    ends, names = [], []
    for e in sorted(entries, key=lambda x: (x['start_us'], x['end_us'])):
        lane = next((i for i, end in enumerate(ends) if end <= e['start_us']), len(ends))
        if lane == len(ends):
            ends.append(e['end_us'])
            names.append(f'{prefix}_{lane+1:02}')
        else:
            ends[lane] = e['end_us']
        e['track_name'] = names[lane]
    return names


def mg_keyframes(e, height):
    duration = e['end_us'] - e['start_us']
    anim = e.get('animation', {})
    entry, exit = anim.get('entry_us', 180000), anim.get('exit_us', 140000)
    move = anim.get('move_y_px', 8)
    points = {0, duration, entry, duration - exit}
    points.update(round(entry * n / 4) for n in range(1, 4))
    loop = anim.get('loop')
    if loop:
        points.update(range(0, duration + 1, min(120000, max(1, loop['period_us']//12))))
    rows = []
    for at in sorted(points):
        progress = 1 if entry == 0 else min(1, at / entry)
        eased = progress * progress * (3-2*progress)
        y = -move * (1-eased) * 2/height
        alpha = min(1, at/entry if entry else 1, (duration-at)/exit if exit else 1)
        if loop:
            phase = (at % loop['period_us']) / loop['period_us']
            if loop['mode'] == 'thought_pulse':
                alpha *= .7 + .3 * (.5 - .5 * math.cos(2*math.pi*phase))
            else:
                y += loop.get('amplitude_px', 0) * 2/height * (phase-.5)
                alpha *= math.sin(math.pi*phase)**2
        rows.append({'at_us': at, 'y': y, 'alpha': max(0, min(1, alpha))})
    return rows


def visual_keyframe_rows(e, width, height):
    """Absolute native transform values; custom scale pivots at the actual PNG bbox center."""
    custom=e.get('animation',{}).get('keyframes')
    if custom:
        b=e['bbox_px'];cx=(b[0]+b[2])/2;cy=(b[1]+b[3])/2
        rows=[]
        for p in custom:
            scale=p.get('scale',1.0)
            rows.append({'at_us':p['at_us'],'scale':scale,'x':(1-scale)*(2*cx/width-1)+2*p.get('dx_px',0)/width,'y':(1-scale)*(1-2*cy/height)-2*p.get('dy_px',0)/height,'alpha':p.get('alpha',1.0)})
        return rows
    return [{'at_us':p['at_us'],'scale':1.0,'x':0.0,'y':p['y'],'alpha':p['alpha']} for p in mg_keyframes(e,height)]


def text_keyframe_rows(e, width, height):
    """Native text is already located at its bbox centre, unlike full-canvas PNGs.

    Position values are absolute canvas fractions. Keep alpha=1 because this
    vendored native text serializer does not support text alpha keyframes.
    Editable text reveals use short scale/position entry and actual clip timing.
    """
    b = e['bbox_px']; cx = (b[0]+b[2])/2; cy = (b[1]+b[3])/2
    custom = e.get('animation', {}).get('keyframes')
    if custom:
        require(all(abs(p.get('alpha',1)-1)<1e-10 for p in custom),
                'Native editable text has no alpha keyframe support; use timed clip/scale/position')
        return [{'at_us':p['at_us'], 'scale':p.get('scale',1.0),
                 'x':2*(cx+p.get('dx_px',0))/width-1,
                 'y':1-2*(cy+p.get('dy_px',0))/height,
                 'alpha':1.0} for p in custom]
    return [{'at_us':p['at_us'], 'scale':1.0, 'x':2*cx/width-1,
             'y':1-2*cy/height+p['y'], 'alpha':1.0}
            for p in mg_keyframes(e,height)]


def local_transition_rows(kind, duration_us, direction='left', prior_pose=None):
    """Position-only local transitions, using real stored media rather than effect IDs.

    slide_reveal moves a still of the prior end pose off the new picture.
    cover_wipe covers the cut at its midpoint and continues in the same direction.
    Neither is a two-picture push or a downloaded online transition resource.
    """
    require(kind in ('slide_reveal','cover_wipe'), 'Unsupported local transition row kind')
    require(direction in ('left','right','up','down'), 'Unsupported direction')
    p = prior_pose or {'scale':1,'x':0,'y':0}
    sign = -1 if direction in ('left','down') else 1
    axis = 'x' if direction in ('left','right') else 'y'
    rows=[]
    for j in range(17):
        t=j/16; eased=t*t*(3-2*t)
        row={'at_us':round(duration_us*t),'scale':p['scale'] if kind=='slide_reveal' else 1.0,
             'x':p['x'] if kind=='slide_reveal' else 0.0,
             'y':p['y'] if kind=='slide_reveal' else 0.0,'alpha':1.0}
        if kind=='slide_reveal':
            endpoint=sign*(1+p['scale']+.04)
            row[axis]=p[axis]+(endpoint-p[axis])*eased
        else:
            row[axis]=-sign*2.06*(1-2*eased)
        rows.append(row)
    return rows


def apply_changed_keyframes(seg, rows, d, allow_alpha=True):
    """Keep editable XY pairing: human 30fps probe C is smooth, X-only A shakes.

    Constant Y is a compatibility channel, not a claimed new visual event.
    Actual nonconstant Y values/times remain the authored curve unchanged.
    """
    first=rows[0]
    seg.clip_settings.scale_x=seg.clip_settings.scale_y=first.get('scale',1)
    seg.clip_settings.transform_x=first.get('x',0);seg.clip_settings.transform_y=first.get('y',0)
    if allow_alpha:seg.clip_settings.alpha=first.get('alpha',1)
    props=[(d.KeyframeProperty.uniform_scale,'scale'),(d.KeyframeProperty.position_x,'x'),(d.KeyframeProperty.position_y,'y')]
    if allow_alpha:props.append((d.KeyframeProperty.alpha,'alpha'))
    x_values=[p.get('x',0) for p in rows]
    has_x_motion=max(x_values)-min(x_values)>1e-10
    for prop,key in props:
        values=[p.get(key,1 if key in ('scale','alpha') else 0) for p in rows]
        if max(values)-min(values)>1e-10 or (key=='y' and has_x_motion):
            for p,value in zip(rows,values):seg.add_keyframe(prop,p['at_us'],value)


CJK_CHARACTER = re.compile(r'[\u3000-\u303f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]')


def canonical_subtitle_text(text):
    """Ignore formatting newlines without inserting artificial spaces between CJK glyphs."""
    text=text.replace('\r\n','\n').replace('\r','\n')
    pattern=re.compile(r'(\S)[ \t]*\n[ \t]*(\S)')
    def join(match):
        a,b=match.group(1),match.group(2)
        separator='' if CJK_CHARACTER.search(a) or CJK_CHARACTER.search(b) else ' '
        return a+separator+b
    while pattern.search(text):text=pattern.sub(join,text)
    return ' '.join(text.split())


def subtitle_layout(cue,style,width,height):
    """Preserve every word/punctuation and cue times, allowing only layout whitespace changes.

    The native size-to-pixel mapping is not independently verified by a native render;
    use a conservative 12px/native-unit envelope and save the actual font metrics.
    """
    from PIL import ImageFont
    from runtime_paths import find_font
    font_path=find_font(style.get('measurement_font'))
    require(font_path.is_file(),'Subtitle measurement font missing')
    font_px=round(style.get('size',4.4)*12)
    font=ImageFont.truetype(str(font_path),font_px)
    max_width=round(width*style.get('max_line_width',.86))-12
    text=canonical_subtitle_text(cue['text'])
    if font.getlength(text)<=max_width:lines=[text]
    else:
        candidates=[]
        for i in range(1,len(text)):
            left,right=text[i-1],text[i]
            if not (left.isspace() or right.isspace() or CJK_CHARACTER.search(left) or CJK_CHARACTER.search(right)):
                continue
            # Avoid opening a line with closing punctuation or leaving an opening mark behind.
            if right in '，。！？；：、）》】』」…,.!?;:%' or left in '（《【『「(':
                continue
            a,b=text[:i].rstrip(),text[i:].lstrip()
            if not a or not b:continue
            aw,bw=font.getlength(a),font.getlength(b)
            if max(aw,bw)<=max_width:candidates.append((abs(aw-bw),i,a,b))
        require(bool(candidates),f'Fixed subtitle style cannot fit cue {cue["number"]} into 2 lines; do not silently shrink/delete')
        _,_,a,b=min(candidates);lines=[a,b]
    native_y=style.get('y',-.78)
    center_y=(1-native_y)*height/2
    ascent,descent=font.getmetrics();line_height=ascent+descent
    total_height=line_height*len(lines)+8*(len(lines)-1)+8
    max_line=max(font.getlength(x) for x in lines)+8
    bbox=[width/2-max_line/2,center_y-total_height/2,width/2+max_line/2,center_y+total_height/2]
    require(bbox[0]>=width*.05 and bbox[2]<=width*.95 and bbox[1]>=height*.77 and bbox[3]<=height*.97,f'Subtitle conservative font envelope exceeds fixed bottom safe rectangle cue {cue["number"]}')
    return {'text':'\n'.join(lines),'original_text':cue['text'],'font_file':str(font_path),'font_sha256':sha(font_path),'measurement_font_px':font_px,'native_unit_pixel_envelope_factor':12,'measurement_bbox_px':bbox,'native_size':style.get('size',4.4),'native_transform_y':native_y,'native_render_verified':False}


def stage_file(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    require(sha(source) == sha(destination), f'Staged source changed: {source}')
    return destination


def build(plan_path, output):
    from motion_compatibility import require_build_approval
    require_build_approval(read(plan_path))
    started = time.perf_counter()
    checked = validate_plan(plan_path)
    plan_path, plan = load_plan(plan_path)
    base, output = plan_path.parent, Path(output).resolve()
    cover_ffmpeg = None
    if plan['shots'][0].get('media_type') == 'video':
        cover_ffmpeg = os.environ.get('EXPLAINER_VIDEO_FFMPEG') or os.environ.get('SCIENCE_VIDEO_FFMPEG') or shutil.which('ffmpeg')
        if not cover_ffmpeg:
            try:
                import imageio_ffmpeg
                cover_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except (ImportError, RuntimeError):
                pass
        require(cover_ffmpeg and Path(cover_ffmpeg).is_file(), 'Set EXPLAINER_VIDEO_FFMPEG to an existing FFmpeg executable for the video cover frame')
    require(not output.exists(), f'Output exists; refuse overwrite: {output}')
    require(output.parent != output, 'Invalid output directory')
    d, (Image, ImageOps) = native(), image_lib()
    c = plan['canvas']; w, h, total = c['width'], c['height'], c['duration_us']
    safe_name(output.name)
    output.parent.mkdir(parents=True, exist_ok=True)
    project = d.DraftFolder(str(output.parent)).create_draft(output.name, w, h, c['fps'], maintrack_adsorb=False)
    media = output / 'media'
    snapshot = copy.deepcopy(plan)
    snapshot['authored_plan_sha256'] = sha(plan_path)
    provenance = stage_science_ledger(plan, base, snapshot, output)
    annotation_source=plan.get('annotation_contract',{}).get('source_file')
    if annotation_source:
        original=resolve(base,annotation_source)
        require(sha(original)==plan['annotation_contract']['source_sha256'],'Annotation contract bytes changed')
        frozen=stage_file(original,output/'review/annotations/contract.original.json')
        snapshot['annotation_contract']['source_file']=frozen.relative_to(output).as_posix()
        provenance.append({'kind':'annotation_contract','source_sha256':sha(original),
                           'portable_sha256':sha(frozen),'original_bytes_preserved':True})
    mechanism_info = mechanism_registry_inputs(plan, base)
    if mechanism_info:
        frozen_registry = stage_file(mechanism_info['path'], output/'review/mechanisms/registry.original.json')
        snapshot['mechanism_registry_file'] = frozen_registry.relative_to(output).as_posix()
        evidence_dir = output/'review/mechanisms/state_samples'
        evidence_dir.mkdir(parents=True, exist_ok=True)
        for group in mechanism_info['groups'].values():
            for sample in group['state_evidence_samples']:
                source = (resolve(base, plan['mechanism_evidence_directory'])/Path(sample['proxy_file']).name
                    if plan.get('mechanism_evidence_directory') else Path(sample['proxy_file']))
                stage_file(source, evidence_dir/source.name)
        snapshot['mechanism_evidence_directory'] = evidence_dir.relative_to(output).as_posix()
        provenance.append({'kind':'mechanism_registry','source_sha256':sha(mechanism_info['path']),
            'portable_sha256':sha(frozen_registry),'original_bytes_preserved':True})
    for kind in ('audio', 'srt', 'manuscript'):
        if not plan['sources'].get(kind):
            continue
        src = resolve(base, plan['sources'][kind])
        dest = stage_file(src, media / 'sources' / (kind + src.suffix.lower()))
        snapshot['sources'][kind] = dest.relative_to(output).as_posix()
    shots = copy.deepcopy(plan['shots'])
    for i, s in enumerate(shots):
        src = resolve(base, s['image'])
        original = stage_file(src, media / 'originals' / (s['id'] + src.suffix.lower()))
        snapshot['shots'][i]['image'] = original.relative_to(output).as_posix()
        snapshot['shots'][i]['image_sha256'] = sha(src)
        if s.get('reuse_review'):
            evidence=resolve(base,s['reuse_review']['evidence_file'])
            staged_evidence=stage_file(evidence,output/'review/reuse'/(s['id']+evidence.suffix.lower()))
            snapshot['shots'][i]['reuse_review']['evidence_file']=staged_evidence.relative_to(output).as_posix()
            s['reuse_review']=copy.deepcopy(snapshot['shots'][i]['reuse_review'])
        if s.get('media_type','image')=='video':
            staged=stage_file(src,media/'main'/(s['id']+src.suffix.lower()))
            s.update(asset_path=str(staged),asset_sha256=sha(staged),camera_keyframes=camera_points(s))
            provenance.append({'kind':'main_video','id':s['id'],'source_sha256':sha(src),'source_copy':str(original),'derived_path':str(staged),'derived_sha256':sha(staged),'method':'byte-identical original official website video'})
            continue
        staged = media / 'main' / (s['id'] + '.png')
        staged.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(src) as raw:
            frame = ImageOps.fit(ImageOps.exif_transpose(raw).convert('RGB'), (w, h), method=Image.Resampling.LANCZOS)
            frame.save(staged)
        s.update(asset_path=str(staged), asset_sha256=sha(staged), camera_keyframes=camera_points(s))
        provenance.append({'kind': 'main_image', 'id': s['id'], 'source_sha256': sha(src), 'source_copy': str(original), 'derived_path': str(staged), 'derived_sha256': sha(staged), 'method': 'EXIF transpose + centered aspect-preserving cover to canvas PNG'})
    mg = copy.deepcopy(plan.get('mg', []))
    for i, e in enumerate(mg):
        if e['type'] == 'text' and e.get('native_font_file'):
            font_source = resolve(base, e['native_font_file'])
            font_dest = stage_file(font_source, media/'fonts'/(sha(font_source)+font_source.suffix.lower()))
            e['native_font_file'] = str(font_dest)
            e['font_file'] = str(font_dest)
            e['native_font_sha256'] = sha(font_dest)
            snapshot['mg'][i]['native_font_file'] = font_dest.relative_to(output).as_posix()
            snapshot['mg'][i]['font_file'] = font_dest.relative_to(output).as_posix()
            snapshot['mg'][i]['native_font_sha256'] = sha(font_dest)
        if e['type'] not in ('png','video'):
            continue
        src = resolve(base, e['asset'])
        original = stage_file(src, media / 'mg_originals' / (e['id'] + src.suffix.lower()))
        snapshot['mg'][i]['asset'] = original.relative_to(output).as_posix()
        art_source=e.get('artwork_provenance', {}).get('source_file')
        if art_source:
            art_path=resolve(base,art_source)
            art_dest=stage_file(art_path,media/'sources'/'artwork'/(sha(art_path)+art_path.suffix.lower()))
            snapshot['mg'][i]['artwork_provenance']['source_file']=art_dest.relative_to(output).as_posix()
        if e['type']=='video':
            dest=stage_file(src,media/'mg'/(e['id']+src.suffix.lower()))
            e.update(asset_path=str(dest),asset_sha256=sha(dest))
            continue
        with Image.open(src) as raw:
            canvas, layout_geometry = prepare_png_canvas(raw, e, (w, h))
        dest = media / 'mg' / (e['id'] + '.png')
        dest.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(dest)
        e.update(asset_path=str(dest), asset_sha256=sha(dest), alpha_bbox_px=list(canvas.getchannel('A').getbbox()),
                 png_layout_geometry=layout_geometry)
    sfx = copy.deepcopy(plan.get('sfx', []))
    for i, e in enumerate(sfx):
        src = resolve(base, e['asset'])
        dest = stage_file(src, media / 'sfx' / (e['id'] + src.suffix.lower()))
        snapshot['sfx'][i]['asset'] = dest.relative_to(output).as_posix()
        e.update(asset_path=str(dest), asset_sha256=sha(dest), end_us=e['start_us']+e['duration_us'])
        if e.get('action_sync'):
            evidence=resolve(base,e['action_sync']['evidence_file'])
            staged_evidence=stage_file(evidence,output/'review/action_sync'/(e['id']+'.json'))
            snapshot['sfx'][i]['action_sync']['evidence_file']=staged_evidence.relative_to(output).as_posix()
            e['action_sync']=copy.deepcopy(snapshot['sfx'][i]['action_sync'])
    cues = parse_srt(resolve(base, plan['sources']['srt']))
    subtitle_rows = copy.deepcopy(cues) if plan.get("render_subtitles", True) else []
    subtracks = lane_assign(subtitle_rows, '字幕')
    mg_tracks = {}
    for layer in sorted({e.get('layer', 1) for e in mg}):
        for kind in ('png', 'text','video'):
            rows = [e for e in mg if e.get('layer', 1) == layer and e['type'] == kind]
            mg_tracks.update({name: kind for name in lane_assign(rows, f'MG_{layer:02}_{kind}')})
    sfx_tracks = lane_assign(sfx, '音效')
    main, transitions, voice = '主画面', '短转场', '源旁白'
    specs = [(d.TrackType.video, main)]
    specs += [(d.TrackType.video if typ in ('png','video') else d.TrackType.text, name) for name, typ in mg_tracks.items()]
    # A cover must cover every explanatory layer while original captions remain
    # above it. Explicit track ordering is part of the transition contract.
    specs += [(d.TrackType.video, transitions)]
    specs += [(d.TrackType.text, name) for name in subtracks] + [(d.TrackType.audio, voice)] + [(d.TrackType.audio, name) for name in sfx_tracks]
    project.append_tracks([d.TrackSpec(typ, name) for typ, name in specs])
    expected = []
    def add(seg, track, kind, info):
        project.add_segment(seg, track)
        if kind in ('subtitle', 'mg_text'):
            project.materials.speeds.append(seg.speed)
        exported = seg.export_json()
        expected.append({'kind': kind, 'id': info.get('id', str(info.get('number', 'voice'))), 'segment_id': seg.segment_id, 'track_name': track, 'start_us': exported['target_timerange']['start'], 'duration_us': exported['target_timerange']['duration'], 'asset_path': info.get('asset_path'), 'asset_sha256': info.get('asset_sha256'), 'text': info.get('text'), 'clip': exported.get('clip'), 'keyframes': exported.get('common_keyframes', []), 'source_timerange': exported.get('source_timerange'), 'volume': exported.get('volume'),'action_sync':copy.deepcopy(info.get('action_sync'))})
        info['segment_id'] = seg.segment_id
        return seg
    for s in shots:
        first = s['camera_keyframes'][0]
        source_timerange=None
        if s.get('media_type','image')=='video':source_timerange=d.trange(s.get('source_start_us',0),round((s['end_us']-s['start_us'])*s.get('playback_speed',1)))
        seg = d.VideoSegment(s['asset_path'], d.trange(s['start_us'], s['end_us']-s['start_us']), source_timerange=source_timerange, volume=0, clip_settings=d.ClipSettings(scale_x=first['scale'], scale_y=first['scale'], transform_x=first['x'], transform_y=first['y']))
        apply_changed_keyframes(seg,s['camera_keyframes'],d,False)
        add(seg, main, 'main_image', s)
    audio_path = resolve(output, snapshot['sources']['audio'])
    audio_material = d.AudioMaterial(str(audio_path))
    if audio_path.suffix.lower() == '.wav':
        exact, proof = media_duration(audio_path)
        require(0 <= exact-audio_material.duration < 1000, 'Unexpected PCM duration mismatch; do not change content or guess extension')
        audio_material.duration = exact
    require(audio_material.duration >= total, 'Native audio parser duration is shorter than proved original source')
    seg = d.AudioSegment(audio_material, d.trange(0, total), source_timerange=d.trange(0, total), volume=1)
    add(seg, voice, 'narration', {'id': 'narration', 'asset_path': str(audio_path), 'asset_sha256': sha(audio_path)})
    style = plan.get('subtitle_style', {})
    layouts=[]
    for cue in subtitle_rows:
        layout=subtitle_layout(cue,style,w,h);layouts.append({'number':cue['number'],**layout})
        cue['original_text']=cue['text'];cue['text']=layout['text']
        seg = d.TextSegment(cue['text'], d.trange(cue['start_us'], cue['end_us']-cue['start_us']), style=d.TextStyle(size=style.get('size',4.4), bold=True, align=1, auto_wrapping=True, max_line_width=style.get('max_line_width',.86)), border=d.TextBorder(width=18), clip_settings=d.ClipSettings(transform_y=style.get('y',-.78)))
        add(seg, cue['track_name'], 'subtitle', cue)
    # Native tracks preserve append order. Author lists are grouped by mechanism,
    # including later-added early scenes; emit clips chronologically into lanes.
    for e in sorted(mg, key=lambda item: (item['start_us'], item['end_us'], item['id'])):
        a, b = e['bbox_px'][:2], e['bbox_px'][2:]
        cx, cy = ((a[0]+b[0])/2)/w, ((a[1]+b[1])/2)/h
        settings = d.ClipSettings(scale_x=1, scale_y=1, transform_x=0 if e['type']=='png' else 2*cx-1, transform_y=0 if e['type']=='png' else 1-2*cy)
        timerange = d.trange(e['start_us'], e['end_us']-e['start_us'])
        if e['type'] in ('png','video'):
            rows=visual_keyframe_rows(e,w,h)
            source_timerange=d.trange(e.get('source_start_us',0),e['end_us']-e['start_us']) if e['type']=='video' else None
            seg = d.VideoSegment(e['asset_path'], timerange, source_timerange=source_timerange, volume=0, clip_settings=settings)
            apply_changed_keyframes(seg,rows,d,True)
        else:
            color = e.get('text_color', '#243C45')
            rgb = tuple(int(color[i:i+2], 16)/255 for i in (1, 3, 5))
            border=None
            if e.get('text_stroke'):
                stroke=e['text_stroke'];require('native_width' in stroke,'Text stroke requires explicit native_width; proxy pixels are not calibrated native units')
                sc=stroke['color'];srgb=tuple(int(sc[i:i+2],16)/255 for i in (1,3,5))
                border=d.TextBorder(color=srgb,width=stroke['native_width'])
            seg = d.TextSegment(e['text'], timerange, style=d.TextStyle(size=e.get('text_size', 6), bold=e.get('text_bold',True), color=rgb, align=1, auto_wrapping=False, max_line_width=.96), border=border, clip_settings=settings)
            rows=text_keyframe_rows(e,w,h)
            apply_changed_keyframes(seg,rows,d,False)
        add(seg, e['track_name'], 'mg_png' if e['type'] in ('png','video') else 'mg_text', e)
    for e in sorted(sfx, key=lambda item: (item['start_us'], item['duration_us'], item['id'])):
        seg = d.AudioSegment(e['asset_path'], d.trange(e['start_us'], e['duration_us']), source_timerange=d.trange(e.get('source_start_us', 0), e['duration_us']), volume=10**(e.get('gain_db', -18)/20))
        add(seg, e['track_name'], 'sfx', e)
    overlay_rows = []
    for i, s in enumerate(shots):
        trans = s.get('incoming_transition', {'type': 'cut'})
        if not i or trans.get('type', 'cut') == 'cut':
            continue
        length, typ = trans['duration_us'], trans['type']
        start = s['start_us']-(length//2 if typ in ('dip_black','cover_wipe') else 0)
        local_rows=None
        if typ == 'dip_black':
            asset = media/'transitions'/'black.png'
            asset.parent.mkdir(exist_ok=True)
            if not asset.exists():
                Image.new('RGB', (w, h), (0, 0, 0)).save(asset)
            seg = d.VideoSegment(str(asset), d.trange(start, length), volume=0)
            seg.clip_settings.alpha=0
            for at, alpha in ((0, 0), (length//2, 1), (length, 0)):
                seg.add_keyframe(d.KeyframeProperty.alpha, at, alpha)
        elif typ in ('slide_reveal','cover_wipe'):
            if typ == 'slide_reveal':
                prior=shots[i-1];asset=Path(prior['asset_path'])
                local_rows=local_transition_rows(typ,length,trans.get('direction','left'),prior['camera_keyframes'][-1])
            else:
                color=trans.get('color','#FFD568')
                asset=media/'transitions'/('cover_'+color[1:]+'.png');asset.parent.mkdir(exist_ok=True)
                if not asset.exists():Image.new('RGB',(w,h),color).save(asset)
                local_rows=local_transition_rows(typ,length,trans.get('direction','left'))
            seg=d.VideoSegment(str(asset),d.trange(start,length),volume=0)
            apply_changed_keyframes(seg,local_rows,d,False)
        else:
            prior = shots[i-1]; asset = Path(prior['asset_path']); p = prior['camera_keyframes'][-1]
            prior_source=None
            if prior.get('media_type','image')=='video':
                prior_speed=prior.get('playback_speed',1)
                source_start=prior.get('source_start_us',0)+round((prior['end_us']-prior['start_us']-length)*prior_speed)
                prior_source=d.trange(source_start,round(length*prior_speed))
            seg = d.VideoSegment(str(asset), d.trange(start, length), source_timerange=prior_source, volume=0, clip_settings=d.ClipSettings(scale_x=p['scale'], scale_y=p['scale'], transform_x=p['x'], transform_y=p['y']))
            for j in range(9):
                t=j/8; eased=t*t*(3-2*t)
                seg.add_keyframe(d.KeyframeProperty.alpha, round(length*t), 1-eased)
        row = {'id': f'T{i:03}', 'asset_path': str(asset), 'asset_sha256': sha(asset), 'type': typ,
               'direction':trans.get('direction'),'start_us':start,'duration_us':length,
               'actual_local_transform_rows':local_rows,
               'does_not_depend_on_downloaded_effect_resources':True}
        add(seg, transitions, 'transition', row)
        overlay_rows.append(row)
    project.save()
    content = read(output/'draft_content.json')
    native_fonts = {e['segment_id']: e for e in mg if e['type']=='text' and e.get('native_font_file')}
    font_materials = {seg['material_id']: native_fonts[seg['id']] for track in content['tracks'] for seg in track['segments'] if seg['id'] in native_fonts}
    for material in content['materials']['texts']:
        if material['id'] in font_materials:
            font_row = font_materials[material['id']]
            text_json = json.loads(material['content'])
            text_json['styles'][0]['font'] = {'id':'', 'path':font_row['native_font_file']}
            material['content'] = json.dumps(text_json, ensure_ascii=False)
        if material.get('type')=='subtitle':
            material['force_apply_line_max_width']=True
            material['line_spacing']=.012
    did, now = str(uuid.uuid4()).upper(), int(time.time()*1000000)
    content.update(id=did, name=plan['name'], duration=total, create_time=now, update_time=now, static_cover_image_path=str(output/'draft_cover.jpg'))
    content['config']['export_range'] = {'start': 0, 'duration': total}
    write(output/'draft_content.json', content)
    cover_asset=shots[0]['asset_path']
    if shots[0].get('media_type')=='video':
        cover_asset=media/'cover_frame.png'
        subprocess.run([cover_ffmpeg,'-y','-i',shots[0]['asset_path'],'-frames:v','1',str(cover_asset)],capture_output=True,check=True)
    with Image.open(cover_asset) as im:
        im.thumbnail((640, 360)); im.convert('RGB').save(output/'draft_cover.jpg', quality=90)
    meta = read(output/'draft_meta_info.json')
    meta.update(draft_id=did, draft_name=plan['name'], draft_fold_path=str(output), draft_root_path=str(output.parent), draft_json_file=str(output/'draft_content.json'), draft_cover=str(output/'draft_cover.jpg'), draft_new_version=content['new_version'], draft_type='', tm_duration=total, tm_draft_create=now, tm_draft_modified=now, tm_draft_removed=0, draft_timeline_materials_size=sum(p.stat().st_size for p in media.rglob('*') if p.is_file()))
    write(output/'draft_meta_info.json', meta)
    write(output/'plan.snapshot.json', snapshot)
    files = {p.relative_to(output).as_posix(): sha(p) for p in media.rglob('*') if p.is_file()}
    manifest = {'schema_version': 1, 'draft_path': str(output), 'draft_id': did, 'canvas': c, 'sources': snapshot['sources'], 'cues': cues, 'image_track': shots, 'mg_elements': mg, 'sound_effects': sfx, 'transition_overlays': overlay_rows, 'expected_segments': expected, 'asset_files': files, 'provenance': provenance, 'native_visual_verified': False, 'editor_open_verified': False, 'gui_used': False, 'design_baseline': 'Proven generic editable draft mechanics; science topic, IP and visual direction require separate real inputs; native sample required'}
    write(output/'timeline_manifest.json', manifest)
    write(output/'subtitle_layout_metrics.json',{'schema_version':1,'uniform_style':style,'cue_count':len(layouts),'original_srt_sha256':plan['sources']['srt_sha256'],'rows':layouts,'native_render_verified':False,'scope':'all native fields and conservative real-font metric envelope; not actual Jianying render'})
    report = validate_draft(output)
    report.update(draft_path=str(output), build_seconds=round(time.perf_counter()-started, 6), source_plan=str(plan_path), original_audio_modified=False, original_subtitles_modified=False)
    write(output/'build_report.json', report)
    return report


def text_content(material):
    value = material.get('content', '')
    if isinstance(value, str):
        try:
            return json.loads(value).get('text', '')
        except (ValueError, AttributeError):
            return value
    return value.get('text', '') if isinstance(value, dict) else ''


def validate_draft(draft, plan_path=None):
    draft = Path(draft).resolve()
    snapshot = draft/'plan.snapshot.json'
    require(snapshot.is_file(), 'Self-contained plan snapshot missing')
    source_check = validate_plan(snapshot)
    plan = read(snapshot)
    if plan_path:
        validate_plan(plan_path)
        requested_path, requested = load_plan(plan_path)
        require(sha(requested_path) == plan.get('authored_plan_sha256'), 'External authored plan bytes differ from build input')
        require(requested.get('mechanism_registry_sha256') == plan.get('mechanism_registry_sha256'), 'External mechanism registry differs from saved draft')
        external_science = science_ledger_inputs(requested, requested_path.parent)
        frozen_science = science_ledger_inputs(plan, draft)
        require(bool(external_science) == bool(frozen_science), 'External science ledger presence differs from saved draft')
        if external_science:
            require(external_science['digest'] == plan.get('science_source_ledger_original_sha256'), 'External science ledger differs from saved draft')
            require([(i, digest) for i, _, digest in external_science['files']] == [(i, digest) for i, _, digest in frozen_science['files']], 'External science source files differ from saved draft')
        require(requested['canvas'] == plan['canvas'] and requested['sources']['audio_sha256'] == plan['sources']['audio_sha256'] and requested['sources']['srt_sha256'] == plan['sources']['srt_sha256'], 'External plan source/canvas differs from draft')
        require([(s['id'], s['start_us'], s['end_us']) for s in requested['shots']] == [(s['id'], s['start_us'], s['end_us']) for s in plan['shots']], 'External shot timing differs from saved draft')
        require(requested.get('subtitle_style', {}) == plan.get('subtitle_style', {}), 'External subtitle style differs from saved draft')
        require(requested.get('render_subtitles', True) == plan.get('render_subtitles', True), 'External subtitle render policy differs from saved draft')
        for a, b in zip(requested['shots'], plan['shots']):
            require(a.get('camera', {}) == b.get('camera', {}) and a.get('incoming_transition', {}) == b.get('incoming_transition', {}), 'External camera/transition differs from saved draft')
            require(sha(resolve(requested_path.parent, a['image'])) == b['image_sha256'], 'External main image differs from saved draft')
        for category in ('mg', 'sfx'):
            incoming, frozen = requested.get(category, []), plan.get(category, [])
            require(len(incoming) == len(frozen), f'External {category} count differs from saved draft')
            for a, b in zip(incoming, frozen):
                # Relocated font paths must compare by exact bytes, like image assets.
                relocated_keys = {'asset', 'font_file', 'native_font_file'}
                for font_key in ('font_file', 'native_font_file'):
                    require(bool(a.get(font_key)) == bool(b.get(font_key)), 'External font presence differs: '+a['id'])
                    if a.get(font_key):
                        require(sha(resolve(requested_path.parent,a[font_key])) == sha(resolve(draft,b[font_key])), 'External font bytes differ: '+a['id'])
                comparable_a = copy.deepcopy({k:v for k,v in a.items() if k not in relocated_keys})
                comparable_b = copy.deepcopy({k:v for k,v in b.items() if k not in relocated_keys})
                pa,pb = a.get('artwork_provenance',{}), b.get('artwork_provenance',{})
                require(bool(pa.get('source_file')) == bool(pb.get('source_file')), 'Artwork source presence differs: '+a['id'])
                if pa.get('source_file'):
                    require(sha(resolve(requested_path.parent,pa['source_file'])) == sha(resolve(draft,pb['source_file'])), 'Artwork source bytes differ: '+a['id'])
                    comparable_a['artwork_provenance'].pop('source_file')
                    comparable_b['artwork_provenance'].pop('source_file')
                for comparable in (comparable_a, comparable_b):
                    if comparable.get('action_sync'):
                        comparable['action_sync'].pop('evidence_file', None)
                require(comparable_a == comparable_b, f'External {category} plan differs from saved draft')
                if a.get('asset'):
                    require(sha(resolve(requested_path.parent,a['asset'])) == sha(resolve(draft,b['asset'])), f'External {category} asset differs from saved draft')
    doc, meta, manifest = (read(draft/name) for name in ('draft_content.json', 'draft_meta_info.json', 'timeline_manifest.json'))
    total = plan['canvas']['duration_us']
    require(doc.get('duration') == meta.get('tm_duration') == total and doc.get('id') == meta.get('draft_id') == manifest.get('draft_id'), 'Native draft identity/duration mismatch')
    require(doc.get('name') == meta.get('draft_name'), 'Native draft name mismatch')
    require(doc.get('config', {}).get('export_range') == {'start': 0, 'duration': total}, 'Native export range differs from complete source audio')
    require(doc.get('canvas_config', {}).get('width') == plan['canvas']['width'] and doc.get('canvas_config', {}).get('height') == plan['canvas']['height'], 'Native canvas mismatch')
    require(Path(meta['draft_fold_path']).resolve() == draft and Path(meta['draft_json_file']).resolve() == draft/'draft_content.json', 'Native metadata relocation incomplete')
    require(Path(meta['draft_cover']).is_file(), 'Native draft cover missing')
    materials = {}
    for values in doc.get('materials', {}).values():
        if not isinstance(values, list):
            continue
        for m in values:
            if isinstance(m, dict) and m.get('id'):
                require(m['id'] not in materials, 'Duplicate native material ID')
                materials[m['id']] = m
    segments, track_by_segment = {}, {}
    track_names = [t.get('name') for t in doc.get('tracks', [])]
    require(len(track_names) == len(set(track_names)), 'Duplicate track names')
    for index, track in enumerate(doc.get('tracks', [])):
        last_end = 0
        for seg in track.get('segments', []):
            timerange = seg.get('target_timerange', {})
            a, length = timerange.get('start'), timerange.get('duration')
            require(isinstance(a, int) and isinstance(length, int) and 0 <= a < a+length <= total and a >= last_end, 'Native track overlap or range outside narration')
            last_end = a+length
            require(seg.get('id') not in segments, 'Duplicate native segment ID')
            segments[seg['id']], track_by_segment[seg['id']] = seg, track
            require(seg.get('render_index') == index, 'Native layer render order differs from track order')
            require(seg.get('material_id') in materials, 'Dangling native material reference')
            require(all(mid in materials for mid in seg.get('extra_material_refs', [])), 'Dangling extra material/speed reference')
            curves={k['property_type']:k for k in seg.get('common_keyframes', [])}
            if 'KFTypePositionX' in curves:
                require('KFTypePositionY' in curves,'V6 X channel lacks mandatory paired Y: '+seg['id'])
                require([p['time_offset'] for p in curves['KFTypePositionX']['keyframe_list']]==[p['time_offset'] for p in curves['KFTypePositionY']['keyframe_list']],'V6 paired XY nodes do not match: '+seg['id'])
            for kf in seg.get('common_keyframes', []):
                points = kf.get('keyframe_list', [])
                times = [p.get('time_offset') for p in points]
                require(times == sorted(set(times)) and all(isinstance(t, int) and 0 <= t <= length for t in times), 'Invalid native keyframe times')
                require(all(all(isinstance(v, (int, float)) and math.isfinite(v) for v in p.get('values', [])) for p in points), 'Non-finite native keyframe value')
    require(set(segments) == {r['segment_id'] for r in manifest['expected_segments']}, 'Native segments added, missing or duplicated')
    for row in manifest['expected_segments']:
        seg, track = segments[row['segment_id']], track_by_segment[row['segment_id']]
        material = materials[seg['material_id']]
        require(track['name'] == row['track_name'] and seg['target_timerange'] == {'start': row['start_us'], 'duration': row['duration_us']}, f'Native timing/track mismatch: {row["id"]}')
        for native_key, expected_key in (('clip', 'clip'), ('common_keyframes', 'keyframes'), ('source_timerange', 'source_timerange'), ('volume', 'volume')):
            require(seg.get(native_key) == row.get(expected_key), f'Native {native_key} differs from saved accepted plan: {row["id"]}')
        if row['asset_path']:
            require(Path(material.get('path', '')).resolve() == Path(row['asset_path']).resolve() and sha(row['asset_path']) == row['asset_sha256'], f'Native material source changed: {row["id"]}')
        if row['text'] is not None:
            require(text_content(material) == row['text'], f'Editable native text changed: {row["id"]}')
            require(track['type'] == 'text', 'Subtitle/MG text is not editable native text')
            require(all(k['property_type'] != 'KFTypeAlpha' for k in seg.get('common_keyframes', [])), 'Unsupported native text alpha keyframes')
            if row['kind']=='mg_text':
                incoming=next(e for e in plan['mg'] if e['id']==row['id'])
                content=json.loads(material['content']);styles=content.get('styles',[])
                require(len(styles)==1,'Native annotation unexpectedly contains mixed styles: '+row['id'])
                style=styles[0];color=incoming.get('text_color','#243C45')
                rgb=[int(color[i:i+2],16)/255 for i in (1,3,5)]
                require(abs(style.get('size',0)-incoming.get('text_size',6))<1e-10,'Native annotation font size differs from contract: '+row['id'])
                require(style.get('bold') is incoming.get('text_bold',True),'Native annotation weight differs from contract: '+row['id'])
                if incoming.get('native_font_file'):
                    font_path = resolve(draft, incoming['native_font_file'])
                    require(Path(style.get('font',{}).get('path','')).resolve()==font_path, 'Native annotation font path differs: '+row['id'])
                    require(sha(font_path)==incoming['native_font_sha256'], 'Native annotation font bytes changed: '+row['id'])
                require(style.get('fill',{}).get('content',{}).get('solid',{}).get('color')==rgb,'Native annotation color differs from contract: '+row['id'])
                require(not material.get('background_style') and not material.get('background_alpha'),'Unexpected uncalibrated native annotation backing: '+row['id'])
        if row['kind'] == 'narration':
            require(seg['target_timerange'] == {'start': 0, 'duration': total} and seg['source_timerange'] == {'start': 0, 'duration': total} and seg['volume'] == 1 and not seg.get('common_keyframes'), 'Source narration was trimmed, retimed, faded or mixed')
            require(sha(material['path']) == plan['sources']['audio_sha256'], 'Narration is not original source audio bytes')
    # Match saved native clips to independent semantic input, not just writer receipts.
    input_specs = {}
    for s in plan['shots']:
        input_specs[('main_image', s['id'])] = (s['start_us'], s['end_us'], None)
    for e in plan.get('mg', []):
        kind = 'mg_png' if e['type']=='png' else 'mg_text'
        input_specs[(kind, e['id'])] = (e['start_us'], e['end_us'], e.get('text'))
    for e in plan.get('sfx', []):
        input_specs[('sfx', e['id'])] = (e['start_us'], e['start_us']+e['duration_us'], None)
    actual_specs = {(r['kind'], r['id']): (r['start_us'], r['start_us']+r['duration_us'], r['text']) for r in manifest['expected_segments'] if r['kind'] in ('main_image', 'mg_png', 'mg_text', 'sfx')}
    require(actual_specs == input_specs, 'Native clip timing/text differs from independent semantic plan')
    require(len(actual_specs) == sum(r['kind'] in ('main_image','mg_png','mg_text','sfx') for r in manifest['expected_segments']), 'Duplicate native semantic clip')
    main_by_id = {r['id']: segments[r['segment_id']] for r in manifest['expected_segments'] if r['kind']=='main_image'}
    for s in plan['shots']:
        seg = main_by_id[s['id']]
        if s.get('media_type','image')=='video':
            expected_source={'start':s.get('source_start_us',0),'duration':round((s['end_us']-s['start_us'])*s.get('playback_speed',1))}
            require(seg.get('source_timerange')==expected_source,f'Native selected video source/speed differs: {s["id"]}')
        point_sets = {k['property_type']: k['keyframe_list'] for k in seg.get('common_keyframes', [])}
        require(seg.get('uniform_scale', {}).get('on') is True, 'Main camera must preserve uniform scale')
        for property_type, key in (('KFTypeScaleX', 'scale'), ('KFTypePositionX', 'x'), ('KFTypePositionY', 'y')):
            points = point_sets.get(property_type)
            planned=camera_points(s);values=[p[key] for p in planned]
            x_values=[p['x'] for p in planned]
            paired_constant_y=key=='y' and max(x_values)-min(x_values)>1e-10
            if max(values)-min(values)>1e-10 or paired_constant_y:
                require(points is not None,f'Changing/paired camera property missing: {s["id"]} / {property_type}')
                require([(p['time_offset'], p['values'][0]) for p in points] == [(p['at_us'], p[key]) for p in planned], f'Native camera differs from independent shot plan: {s["id"]}')
            else:require(points is None,f'Constant property has fake keyframes: {s["id"]} / {property_type}')
            clip_value=seg['clip']['scale']['x'] if key=='scale' else seg['clip']['transform'][key]
            require(abs(clip_value-values[0])<1e-10,f'Initial native clip differs from t0 absolute keyframe: {s["id"]}')
    main_rows = [r for r in manifest['expected_segments'] if r['kind'] == 'main_image']
    sfx_native={r['id']:segments[r['segment_id']] for r in manifest['expected_segments'] if r['kind']=='sfx'}
    for effect in plan.get('sfx',[]):
        if not effect.get('action_sync'):continue
        sync=action_sync_time_us(effect,{s['id']:s for s in plan['shots']},draft,{g['id']:g for g in plan.get('mg',[])})
        seg=sfx_native[effect['id']]
        actual_attack=seg['target_timerange']['start']+effect['action_sync']['audio_attack_us']-seg['source_timerange']['start']
        require(abs(actual_attack-sync['visual_target_attack_us'])<=effect['action_sync'].get('tolerance_us',1000),f'Native SFX attack differs from measured visual action: {effect["id"]}')
    require([(r['start_us'], r['start_us']+r['duration_us']) for r in main_rows] == [(s['start_us'], s['end_us']) for s in plan['shots']], 'Main images do not cover exact source tail')
    actual_cues = sorted((r['start_us'], r['start_us']+r['duration_us'], canonical_subtitle_text(r['text'])) for r in manifest['expected_segments'] if r['kind'] == 'subtitle')
    required_cues = sorted((c['start_us'], c['end_us'], canonical_subtitle_text(c['text'])) for c in parse_srt(resolve(draft, plan['sources']['srt']))) if plan.get("render_subtitles", True) else []
    require(actual_cues == required_cues, 'Native subtitles differ from original SRT cue timing/text')
    subtitle_styles=set()
    for row in manifest['expected_segments']:
        if row['kind']!='subtitle':continue
        seg=segments[row['segment_id']];material=materials[seg['material_id']];content=json.loads(material['content'])
        normalized=[{k:v for k,v in style.items() if k!='range'} for style in content['styles']]
        subtitle_styles.add(json.dumps(normalized,sort_keys=True))
        require(len(content['text'].splitlines())<=2,'Native subtitle has more than 2 lines')
        require(seg['clip']['transform']=={'x':0.0,'y':plan['subtitle_style']['y']},'Subtitle moved out of fixed bottom')
        require(not seg.get('common_keyframes'),'Subtitle has stray animation keyframes')
        require(material['force_apply_line_max_width'] is True and material['line_max_width']==plan['subtitle_style']['max_line_width'],'Subtitle native width enforcement differs')
        require(not material.get('background_style') and not material.get('background_alpha'),'Subtitle mixed backing style')
    require(len(subtitle_styles)==(1 if plan.get('render_subtitles',True) else 0),'Native subtitle styles differ from explicit render policy')
    Image, _ = image_lib()
    for path, digest in manifest['asset_files'].items():
        asset = resolve(draft, path)
        require(asset.is_relative_to(draft) and asset.is_file() and sha(asset) == digest, f'Self-contained asset missing/changed: {path}')
    for e in manifest['mg_elements']:
        if e['type'] == 'png':
            with Image.open(e['asset_path']) as im:
                require(im.size == (plan['canvas']['width'], plan['canvas']['height']) and 'A' in im.getbands() and list(im.getchannel('A').getbbox()) == e['alpha_bbox_px'], 'MG is not the accepted full-canvas transparent PNG')
                source_element = next(g for g in plan['mg'] if g['id'] == e['id'])
                with Image.open(resolve(draft, source_element['asset'])) as original:
                    expected_canvas, expected_geometry = prepare_png_canvas(original, source_element,
                        (plan['canvas']['width'], plan['canvas']['height']))
                require(im.convert('RGBA').tobytes() == expected_canvas.tobytes(),
                    f'Native staged PNG pixels differ from actual original/layout: {e["id"]}')
                require(e.get('png_layout_geometry') == expected_geometry,
                    f'Native PNG canvas/visible-ink geometry receipt differs: {e["id"]}')
    counts = source_check['counts']
    counts.update(tracks=len(doc['tracks']), segments=len(segments), asset_files=len(manifest['asset_files']))
    return {'ok': True, 'scope': 'independent native JSON + unchanged assets/source + conservative geometry', 'draft_path': str(draft), 'duration_us': total, 'counts': counts, 'native_visual_verified': False, 'editor_open_verified': False, 'gui_used': False, 'new_machine_native_sample_required': True}


def require_editor_closed():
    if os.name == 'nt':
        result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', "@(Get-Process | Where-Object { $_.ProcessName -match 'Jianying|CapCut|Lveditor' }).Count"], capture_output=True, text=True, check=True)
        require(result.stdout.strip() == '0', 'Jianying/CapCut is running; close the editor before installing any index row')
    else:
        result = subprocess.run(['ps', '-A', '-o', 'comm='], capture_output=True, text=True, check=True)
        require(not re.search(r'Jianying|CapCut|Lveditor', result.stdout, re.IGNORECASE), 'Editor is running; close it before installing')


def rebase(value, old, new):
    if isinstance(value, dict):
        return {k: rebase(v, old, new) for k, v in value.items()}
    if isinstance(value, list):
        return [rebase(v, old, new) for v in value]
    if isinstance(value, str):
        # Text material content is itself JSON; relocate its embedded font path.
        if value.startswith('{') and '"styles"' in value:
            try:
                parsed = json.loads(value)
                changed = rebase(parsed, old, new)
                if changed != parsed:
                    return json.dumps(changed, ensure_ascii=False)
            except (ValueError, TypeError):
                pass
        normalized, prefix = value.replace('\\', '/'), str(old).replace('\\', '/')
        if normalized.casefold() == prefix.casefold():
            return str(new)
        if normalized.casefold().startswith(prefix.casefold()+'/'):
            return str(Path(new)/normalized[len(prefix)+1:])
    return value


def install(draft, library, index_path, name=None):
    # The hash-before-replace check alone has a TOCTOU gap. Hold one kernel lock
    # for this absolute index from the first read through write/readback. A lock
    # next to the index also coordinates installs from different workspaces.
    index_path = Path(index_path).resolve()
    require(index_path.is_file(), 'Explicit index must already exist; no guessed registry paths')
    with resource_lock(index_path, index_path.parent/'.pipeline-locks', timeout=1800) as lease:
        return _install_serialized(draft, library, index_path, name, lease)


def _install_serialized(draft, library, index_path, name=None, coordination=None):
    started = time.perf_counter()
    draft, library, index_path = Path(draft).resolve(), Path(library).resolve(), Path(index_path).resolve()
    require_editor_closed()
    validate_draft(draft)
    require(library.is_dir() and index_path.is_file(), 'Explicit library/index must already exist; no guessed registry paths')
    require(not library.is_relative_to(draft), 'Library cannot be inside source draft')
    before = index_path.read_bytes(); before_sha = hashlib.sha256(before).hexdigest()
    index = json.loads(before.decode('utf-8-sig'))
    require(isinstance(index, dict) and isinstance(index.get('all_draft_store'), list) and all(isinstance(r, dict) for r in index['all_draft_store']), 'Unsupported index schema; expected all_draft_store list')
    source_meta = read(draft/'draft_meta_info.json')
    if name is None:
        name = source_meta['draft_name'][:75] + '_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    safe_name(name)
    require(not any(row.get('draft_name') == name for row in index['all_draft_store']), 'Installed name already registered; refuse duplicate')
    dest = library/name
    require(dest.parent.resolve() == library and not dest.exists(), 'Installed target exists or escapes library; refuse overwrite')
    originals = {str(p): sha(p) for folder in library.iterdir() if folder.is_dir() for filename in ('draft_content.json', 'draft_meta_info.json') if (p := folder/filename).is_file()}
    source_hashes = {str(p): sha(p) for p in draft.rglob('*') if p.is_file()}
    evidence = library/'.pipeline-install-backups'/(dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')+'_'+uuid.uuid4().hex[:6])
    evidence.mkdir(parents=True, exist_ok=False)
    backup = evidence/'root_meta_info.before.json'
    backup.write_bytes(before)
    require(sha(index_path) == before_sha and sha(backup) == before_sha, 'Index changed before backup; stop')
    shutil.copytree(draft, dest)
    state = {'ok': False, 'source': str(draft), 'installed_path': str(dest), 'index_path': str(index_path), 'index_backup': str(backup), 'index_sha256_before': before_sha, 'gui_used': False, 'coordination': dict(coordination or {})}
    try:
        for p in dest.glob('*.json'):
            data = read(p); updated = rebase(data, draft, dest)
            if data != updated:
                write(p, updated)
        did, now = str(uuid.uuid4()).upper(), int(time.time()*1000000)
        require(all(row.get('draft_id') != did for row in index['all_draft_store']), 'Generated ID collision')
        doc, meta, manifest = (read(dest/p) for p in ('draft_content.json', 'draft_meta_info.json', 'timeline_manifest.json'))
        doc.update(id=did, name=name, create_time=now, update_time=now)
        meta.update(draft_id=did, draft_name=name, draft_fold_path=str(dest), draft_root_path=str(library), draft_json_file=str(dest/'draft_content.json'), draft_cover=str(dest/'draft_cover.jpg'), tm_draft_create=now, tm_draft_modified=now, tm_draft_removed=0)
        manifest.update(draft_id=did, draft_path=str(dest))
        write(dest/'draft_content.json', doc); write(dest/'draft_meta_info.json', meta); write(dest/'timeline_manifest.json', manifest)
        checked = validate_draft(dest)
        require(all(sha(p) == digest for p, digest in source_hashes.items()), 'Source candidate changed during installation')
        require(all(sha(p) == digest for p, digest in originals.items()), 'Existing draft header changed during installation')
        updated = copy.deepcopy(index)
        # Native DraftFolder metadata template is the proven standard row schema.
        # Existing rows and unknown root settings are never copied over or changed.
        row = copy.deepcopy(meta)
        row.update(draft_is_invisible=False, streaming_edit_draft_ready=False)
        updated['all_draft_store'].insert(0, row)
        require(updated['all_draft_store'][1:] == index['all_draft_store'], 'Other index rows changed')
        require({k:v for k,v in updated.items() if k!='all_draft_store'} == {k:v for k,v in index.items() if k!='all_draft_store'}, 'Other index settings changed')
        require_editor_closed()
        require(sha(index_path) == before_sha, 'Index changed while installing; stop without clobbering')
        temp = index_path.parent/(index_path.name+'.pipeline.'+uuid.uuid4().hex+'.tmp')
        with temp.open('x', encoding='utf-8') as stream:
            json.dump(updated, stream, ensure_ascii=False, indent=2); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, index_path)
        require(read(index_path) == updated and all(sha(p) == digest for p, digest in originals.items()), 'Installation readback/preservation mismatch')
        state.update(ok=True, draft_id=did, draft_name=name, index_sha256_after=sha(index_path), existing_rows_unchanged=True, existing_drafts_unchanged=True, source_unchanged=True, rows_before=len(index['all_draft_store']), rows_after=len(updated['all_draft_store']), validation=checked, new_machine_native_sample_required=True)
    except Exception as exc:
        state.update(error=str(exc), installed_copy_exists=dest.exists(), index_changed_since_start=sha(index_path)!=before_sha)
        raise
    finally:
        state['elapsed_seconds'] = round(time.perf_counter()-started, 6)
        write(evidence/'installation_result.json', state)
    return state
