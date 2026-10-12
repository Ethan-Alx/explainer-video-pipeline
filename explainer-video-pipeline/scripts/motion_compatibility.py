"""Portable motion invariants and an optional, explicitly declared artwork guard.

Historical human observations belong in the project's evidence, not a universal
probe UUID or an unconditional topic/frame-rate gate. No helper here grants art,
native playback, or perceptual approval.
"""
import hashlib
import math
from pathlib import Path
import re


def require_build_approval(plan):
    """Keep the historical API while avoiding unrelated project approval gates."""
    fps = plan.get('canvas', {}).get('fps')
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not math.isfinite(fps) or fps <= 0:
        raise ValueError('A finite positive frame rate is required')
    return {'policy': 'explicit paired XY; per-project native compatibility evidence',
            'native_fps': fps, 'historical_probe_required': False,
            'native_playback_verified': False, 'visual_approval_granted': False}


def require_new_artwork(plan, base):
    """Reject exact pixel files only when the plan declares rejected byte hashes.

Optional schema: artwork_guard = {rejected_asset_sha256: [64-hex, ...]}.
This guard never labels an unlisted/new asset visually approved.
"""
    guard = plan.get('artwork_guard')
    if guard is None:
        return {'applied': False, 'reason': 'No project-specific artwork guard declared',
                'visual_approval_granted': False}
    if not isinstance(guard, dict) or not isinstance(guard.get('rejected_asset_sha256'), list):
        raise ValueError('artwork_guard requires rejected_asset_sha256 as a list')
    hashes = guard['rejected_asset_sha256']
    if any(not isinstance(h, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', h) for h in hashes):
        raise ValueError('artwork_guard contains an invalid SHA256')
    rejected = {h.lower() for h in hashes}
    candidates = [(e['id'], e['asset']) for e in plan.get('mg', []) if e.get('type') in ('png', 'video')]
    candidates += [(s['id'], s['image']) for s in plan.get('shots', [])]
    for eid, value in candidates:
        path = Path(value)
        if not path.is_absolute():
            path = Path(base) / path
        if not path.is_file():
            raise ValueError('Declared artwork guard asset is missing: ' + str(path))
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in rejected:
            raise ValueError('Declared rejected artwork SHA cannot be reused: ' + eid)
    return {'applied': True, 'rejected_hash_count': len(rejected),
            'checked_asset_count': len(candidates), 'rejected_reuse': 0,
            'visual_approval_granted': False}


def assert_xy_pair(segment):
    curves = {k['property_type']: k['keyframe_list'] for k in segment.get('common_keyframes', [])}
    if 'KFTypePositionX' not in curves:
        return {'has_x': False, 'constant_y': False}
    if 'KFTypePositionY' not in curves:
        raise ValueError('X keyframes exist but paired Y is absent')
    xt = [p['time_offset'] for p in curves['KFTypePositionX']]
    yt = [p['time_offset'] for p in curves['KFTypePositionY']]
    if xt != yt:
        raise ValueError('Paired XY times differ')
    if not xt or xt != sorted(set(xt)):
        raise ValueError('Paired XY times must be nonempty, unique and ordered')
    ys = [p['values'][0] for p in curves['KFTypePositionY']]
    if not all(math.isfinite(v) for v in ys):
        raise ValueError('Nonfinite paired Y value')
    return {'has_x': True, 'point_count': len(xt), 'constant_y': max(ys) - min(ys) <= 1e-10}
