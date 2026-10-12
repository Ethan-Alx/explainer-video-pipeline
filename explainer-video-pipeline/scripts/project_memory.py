"""Persist a small, factual project handoff; never infer visual approval."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import uuid
from datetime import datetime, timezone

SKILL = Path(__file__).resolve().parents[1]

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def write(path, value):
    tmp = path.with_name(path.name + '.writing')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(tmp, path)

def relative(path, project):
    path = Path(path).resolve()
    try:
        return path.relative_to(project).as_posix()
    except ValueError as exc:
        raise ValueError('Keep portable project artifacts inside the project directory: ' + str(path)) from exc

def source_record(project, plan_path):
    if not plan_path:
        return {}
    plan = read(plan_path)
    result = {}
    for source, dest in [('audio','audio'), ('srt','subtitles'), ('manuscript','manuscript')]:
        value = plan.get('sources', {}).get(source)
        if not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = plan_path.parent / path
        if not path.is_file():
            raise ValueError('Referenced source file is missing: ' + str(path))
        result[dest] = {'file': relative(path, project), 'sha256': sha(path), 'status': 'bytes_recorded_not_content_reviewed'}
    return result

def profile_assets(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from profile_assets(child)
    elif isinstance(value, list):
        for child in value:
            yield from profile_assets(child)
    elif isinstance(value, str) and value.startswith('assets/'):
        yield value

def initialise(args):
    from coordination import resource_lock
    project = args.project.resolve()
    with resource_lock(project, lock_root=project / '.pipeline-locks'):
        return _initialise(args)


def _initialise(args):
    project = args.project.resolve()
    project.mkdir(parents=True, exist_ok=True)
    state_path = project / 'project_state.json'
    if state_path.exists():
        raise ValueError('Existing state preserved; use show or checkpoint to continue.')
    state = read(SKILL / 'assets/project_state.example.json')
    profile_path = project / 'channel_profile.json'
    if args.profile:
        if profile_path.exists():
            raise ValueError('Existing channel profile preserved; omit --profile to use it.')
        filename = {'generic': 'generic.example.json', 'science': 'science.example.json', 'psychology': 'psychology.example.json'}[args.profile]
        profile = read(SKILL / 'assets/profiles' / filename)
        for value in set(profile_assets(profile)):
            source = (SKILL / value).resolve()
            if not source.is_relative_to((SKILL/'assets').resolve()) or not source.is_file():
                raise ValueError('Bundled profile asset missing or outside assets: ' + value)
            destination = (project / value).resolve()
            relative(destination, project)
            if destination.exists() and sha(destination) != sha(source):
                raise ValueError('Different project asset already exists: ' + value)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copyfile(source, destination)
        profile['resource_paths_relative_to'] = 'project_root'
        write(profile_path, profile)
    if profile_path.exists():
        profile = read(profile_path)
        state['profile'] = {'file': 'channel_profile.json', 'sha256': sha(profile_path), 'status': profile.get('status','pending')}
    state['project_id'] = project.name
    state['project_instance_id'] = str(uuid.uuid4())
    state['run_id'] = str(uuid.uuid4())
    state['isolation'] = {
        'project_root': str(project),
        'shared_references_read_only': True,
        'browser_sessions': [],
        'download_binding': 'project_instance_id + run_id + task_id + output SHA; never newest Downloads file',
        'global_import': 'serialized by canonical index path OS lock',
    }
    state['title'] = args.title or project.name
    plan = next((project / name for name in ('production_plan.ready.json', 'production_plan.json', 'plan.json') if (project / name).is_file()), None)
    if plan:
        # Use explicit channel display policy; source SRT remains byte-identical.
        preferences = profile.get('production_preferences', {}) if profile_path.exists() else {}
        current = read(plan)
        current['project_identity'] = {
            'project_id': state['project_id'],
            'project_instance_id': state['project_instance_id'],
            'run_id': state['run_id'],
            'project_root': str(project),
        }
        if isinstance(preferences.get('production_policy'), dict):
            current['production_policy'] = copy.deepcopy(preferences['production_policy'])
        if 'render_narration_subtitles' in preferences:
            value = preferences['render_narration_subtitles']
            if type(value) is not bool:
                raise ValueError('Channel subtitle display preference must be boolean')
            current['render_subtitles'] = value
        write(plan, current)
        state['source_hashes'].update(source_record(project, plan))
        state['active_plan'] = {'file': plan.name, 'sha256': sha(plan), 'status': 'exists_not_validated_by_memory_tool'}
    state['last_updated'] = datetime.now(timezone.utc).isoformat()
    write(state_path, state)
    decision = project / 'decision_log.jsonl'
    if not decision.exists():
        decision.write_text('', encoding='utf-8')
    return {'ok': True, 'state': str(state_path), 'profile': state['profile'], 'next_actions': state['next_actions']}

def checkpoint(args):
    from coordination import resource_lock
    project = args.project.resolve()
    with resource_lock(project, lock_root=project / '.pipeline-locks'):
        return _checkpoint(args)


def _checkpoint(args):
    project = args.project.resolve()
    path = project / 'project_state.json'
    state = read(path)
    before = copy.deepcopy(state)
    profile_value = state.get('profile', {}).get('file')
    if profile_value:
        profile_path = (project / profile_value).resolve()
        relative(profile_path, project)
        if profile_path.is_file():
            new_profile_hash = sha(profile_path)
            prior_profile_hash = state.get('profile', {}).get('sha256')
            state['profile']['sha256'] = new_profile_hash
            if prior_profile_hash and prior_profile_hash != new_profile_hash:
                state['profile']['change_since_last_checkpoint'] = {
                    'previous_sha256': prior_profile_hash,
                    'current_sha256': new_profile_hash,
                    'scope_review_required': 'Compare changed fields with current assets; this does not automatically approve or reject them.'
                }
    if args.plan:
        plan = args.plan.resolve()
        if not plan.is_file():
            raise ValueError('Active plan does not exist')
        identity = read(plan).get('project_identity', {})
        if state.get('project_instance_id') and identity.get('project_instance_id') != state['project_instance_id']:
            raise ValueError('Active plan belongs to another project instance or is missing ownership; do not infer ownership from scene IDs')
        state['active_plan'] = {'file': relative(plan, project), 'sha256': sha(plan), 'status': 'bytes_recorded_not_validation'}
        sources = source_record(project, plan)
        for kind, row in sources.items():
            old = state['source_hashes'].get(kind, {})
            if old.get('sha256') and old['sha256'] != row['sha256']:
                raise ValueError('Source changed; create and document an explicit source revision before checkpoint: ' + kind)
        state['source_hashes'].update(sources)
    if args.stage:
        state['stage'] = args.stage
    if args.next_action:
        state['next_actions'] = args.next_action
    state['last_updated'] = datetime.now(timezone.utc).isoformat()
    history = project / 'state_history'
    history.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    write(history / ('project_state_' + stamp + '.json'), before)
    write(path, state)
    return {'ok': True, 'state': str(path), 'stage': state['stage'], 'active_plan': state['active_plan'], 'next_actions': state['next_actions'], 'verification_inferred': False}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    initial = sub.add_parser('init')
    initial.add_argument('--project', type=Path, required=True)
    initial.add_argument('--profile', choices=['generic','science','psychology'])
    initial.add_argument('--title')
    update = sub.add_parser('checkpoint')
    update.add_argument('--project', type=Path, required=True)
    update.add_argument('--plan', type=Path)
    update.add_argument('--stage')
    update.add_argument('--next-action', action='append')
    show = sub.add_parser('show')
    show.add_argument('--project', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = initialise(args) if args.command == 'init' else checkpoint(args) if args.command == 'checkpoint' else read(args.project / 'project_state.json')
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
        return 1
    return 0

if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
