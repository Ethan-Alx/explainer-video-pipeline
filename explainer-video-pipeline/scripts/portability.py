"""Read-only import planning and new-copy relocation; never overwrite an old draft."""
from pathlib import Path
import copy
import datetime as dt
import json
import shutil
import uuid

import draft_engine as e


def install_preview(draft, library, index_path, name=None):
    draft, library, index_path = (Path(p).resolve() for p in (draft, library, index_path))
    checked = e.validate_draft(draft)
    e.require(library.is_dir() and index_path.is_file(), 'Explicit library/index must already exist')
    e.require(not library.is_relative_to(draft), 'Library cannot be inside source draft')
    index = e.read(index_path)
    e.require(isinstance(index, dict) and isinstance(index.get('all_draft_store'), list)
              and all(isinstance(row, dict) for row in index['all_draft_store']), 'Unsupported index schema')
    meta = e.read(draft/'draft_meta_info.json')
    name = name or meta['draft_name'][:75] + '_dry_run_' + uuid.uuid4().hex[:6]
    e.safe_name(name)
    e.require(not any(row.get('draft_name') == name for row in index['all_draft_store']), 'Installed name already registered; refuse duplicate')
    target = library/name
    e.require(target.parent == library and not target.exists(), 'Installed target exists or escapes library; refuse overwrite')
    return {'ok': True, 'dry_run': True, 'files_written': 0, 'index_sha256': e.sha(index_path),
            'source': str(draft), 'proposed_target': str(target), 'index_path': str(index_path),
            'rows_before': len(index['all_draft_store']), 'rows_after_if_installed': len(index['all_draft_store'])+1,
            'editor_must_be_closed_for_actual_install': True, 'native_visual_verified': False,
            'gui_used': False, 'validation': checked}


def relocate(draft, output):
    draft, output = Path(draft).resolve(), Path(output).resolve()
    e.require(not output.exists(), 'Relocated output exists; refuse overwrite')
    e.require(not output.is_relative_to(draft), 'Relocated copy cannot be inside source')
    # An incoming copy from another machine still contains that machine's native paths.
    # Validate only its self-contained sources first; validate ALL rewritten native data below.
    e.validate_plan(draft/'plan.snapshot.json')
    meta=e.read(draft/'draft_meta_info.json')
    recorded_root=Path(meta['draft_fold_path'])
    e.require(recorded_root.is_absolute(), 'Recorded draft root must be absolute')
    manifest=e.read(draft/'timeline_manifest.json')
    for relative,digest in manifest['asset_files'].items():
        asset=e.resolve(draft,relative)
        e.require(asset.is_relative_to(draft) and asset.is_file() and e.sha(asset)==digest,
                  'Incoming portable asset missing/changed: '+relative)
    if recorded_root.resolve()==draft:
        e.validate_draft(draft)
    before = {str(p): e.sha(p) for p in draft.rglob('*') if p.is_file()}
    shutil.copytree(draft, output)
    for p in output.glob('*.json'):
        original = e.read(p)
        updated = e.rebase(e.rebase(original, recorded_root, output), draft, output)
        if updated != original:
            e.write(p, updated)
    # The destination may have a different parent, unlike an install into one library.
    meta = e.read(output/'draft_meta_info.json')
    meta['draft_root_path'] = str(output.parent)
    e.write(output/'draft_meta_info.json', meta)
    check = e.validate_draft(output)
    e.require(all(e.sha(p) == digest for p, digest in before.items()), 'Source changed during relocation')
    result = {'ok': True, 'source': str(draft), 'output': str(output), 'source_unchanged': True,
              'recorded_original_root':str(recorded_root), 'new_id_assigned': False,
              'native_visual_verified': False, 'validation': check}
    e.write(output/'relocation_receipt.json', result)
    return result
