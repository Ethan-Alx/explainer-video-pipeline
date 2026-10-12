"""Check the public source boundary; never print matched secret values."""
from pathlib import Path
import argparse
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'github_credential': re.compile(r'(?:gh[pousr]' + r'_[A-Za-z0-9]{30,}|github_pat' + r'_[A-Za-z0-9_]{35,})'),
    'provider_credential': re.compile(r'\bsk' + r'-[A-Za-z0-9_-]{24,}'),
    'private_key': re.compile(r'-----BEGIN ' + r'(?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'credential_in_url': re.compile(r'https?://[^\s/:]+:[^\s/@]+@'),
    'personal_windows_path': re.compile(r'(?i)[a-z]:[/\\]+Users[/\\]+(?!Public\b|Default\b)[^\s/\\\"<>]+'),
    'personal_posix_path': re.compile(r'/(?:home|Users)/[^\s/\"<>]+'),
    'channel_brand': re.compile('One' + ' More Why|one' + '[_-]more[_-]why'),
}
PRIVATE_NAMES = {'local_environment.json', 'channel_profile.json', 'project_state.json', 'decision_log.jsonl', '.env'}
MEDIA = {'.png', '.jpg', '.jpeg', '.webp', '.mp3', '.wav', '.mp4', '.srt', '.pem', '.key', '.ttf', '.otf', '.ttc', '.zip'}

def audit(root=ROOT, check_manifest=True):
    root = Path(root).resolve()
    problems = []
    count = 0
    for path in root.rglob('*'):
        rel = path.relative_to(root)
        if any(part in ('.git', '__pycache__', '.pytest_cache') for part in rel.parts) or not path.is_file():
            continue
        count += 1
        if path.is_symlink():
            problems.append({'file': rel.as_posix(), 'kind': 'symlink'})
            continue
        if path.name in PRIVATE_NAMES or path.suffix.lower() in MEDIA:
            problems.append({'file': rel.as_posix(), 'kind': 'private_or_media_payload'})
        try:
            text = path.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            problems.append({'file': rel.as_posix(), 'kind': 'unreviewed_binary'})
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                problems.append({'file': rel.as_posix(), 'kind': label})
    manifest_path = root / 'MANIFEST.json'
    if check_manifest and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        records = [('explainer-video-pipeline/' + r['file'], r['sha256']) for r in manifest['skill_files']]
        records += [(r['file'], r['sha256']) for r in manifest['support_files']]
        for rel, digest in records:
            p = (root / rel).resolve()
            if not p.is_relative_to(root) or not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != digest:
                problems.append({'file': rel, 'kind': 'integrity_mismatch'})
        expected = {rel for rel, _ in records} | {'MANIFEST.json'}
        actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file() and not any(part in ('.git', '__pycache__', '.pytest_cache') for part in p.relative_to(root).parts)}
        for rel in actual - expected:
            problems.append({'file': rel, 'kind': 'unmanifested_file'})
    return {'ok': not problems, 'files_checked': count, 'findings': problems, 'boundary': 'Recognized formats, private payloads and manifest checks; not a proof that every possible secret is absent.'}

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--no-manifest', action='store_true')
    args = parser.parse_args()
    result = audit(args.root, not args.no_manifest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
