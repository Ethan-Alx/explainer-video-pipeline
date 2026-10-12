"""Resolve declared local authoring inputs before writing a candidate elsewhere."""
import copy
from pathlib import Path

PATH_FIELDS = {
    'asset', 'image', 'audio', 'srt', 'manuscript', 'file_path', 'source_file',
    'evidence_file', 'font_file', 'measurement_font', 'mechanism_registry_file',
    'science_source_ledger', 'science_source_ledger_original_file',
    'actual_frozen_overlay_file', 'sound_author_file', 'sound_binding_base_file',
    'previous_sound_basis_file',
}
DIRECTORY_FIELDS = {'mechanism_evidence_directory'}


def resolve_declared_paths(value, base):
    """Rebase only actual file-valued fields; never rewrite IDs, URLs or geometry.

    Missing declared references remain missing for the production validators to
    reject. Original JSON bytes are retained separately by the caller's receipt.
    """
    base = Path(base).resolve()
    def visit(item):
        if isinstance(item, list):
            return [visit(v) for v in item]
        if not isinstance(item, dict):
            return copy.deepcopy(item)
        result = {}
        for key, val in item.items():
            if key in DIRECTORY_FIELDS and isinstance(val, str) and '://' not in val:
                path = Path(val)
                if not path.is_absolute() and (base/path).is_dir():
                    result[key] = str((base/path).resolve())
                    continue
            if key in PATH_FIELDS and isinstance(val, str) and '://' not in val:
                path = Path(val)
                if not path.is_absolute() and (base/path).is_file():
                    result[key] = str((base/path).resolve())
                    continue
            result[key] = visit(val)
        return result
    return visit(value)
