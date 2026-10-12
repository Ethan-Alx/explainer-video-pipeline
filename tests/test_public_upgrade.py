"""Generic profiles and preservation tests; isolated local fixtures only."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'explainer-video-pipeline'
sys.path.insert(0, str(SKILL / 'scripts'))
from production_policy import audit_plan, DEFAULT_POLICY

class PublicUpgrade(unittest.TestCase):
    def test_generic_gate_is_explicit_not_brand_selected(self):
        policy = copy.deepcopy(DEFAULT_POLICY)
        policy['channel_id'] = 'my-education-channel'
        plan = {'production_policy': policy, 'shots': [], 'mg': []}
        report = audit_plan(plan)
        self.assertNotEqual(report['status'], 'not_applicable')
        plan['production_policy'].pop('preset_id')
        self.assertEqual(audit_plan(plan)['status'], 'not_applicable')

    def test_profiles_are_pending_and_have_no_host_identity(self):
        for kind in ('generic', 'psychology', 'science'):
            with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
                result = subprocess.run([sys.executable, str(SKILL / 'scripts/project_memory.py'), 'init', '--project', tmp, '--profile', kind], capture_output=True, text=True, encoding='utf-8')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                profile = json.loads((Path(tmp) / 'channel_profile.json').read_text(encoding='utf-8'))
                self.assertIsNone(profile['identity']['reference_file'])
                self.assertIsNone(profile['channel_name'])
                self.assertNotIn('render_narration_subtitles', profile['production_preferences'])
                self.assertEqual(profile['production_preferences']['production_policy']['preset_id'], 'explainer_v72')

    def test_existing_profile_and_subtitles_survive_new_state(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            p = Path(tmp)
            profile = {'channel_name': 'My Psychology', 'status': 'configured', 'identity': {'reference_file': 'my-own-ip.png'}, 'production_preferences': {'render_narration_subtitles': True, 'production_policy': {**DEFAULT_POLICY, 'channel_id': 'my-psych'}}}
            original = json.dumps(profile).encode()
            (p / 'channel_profile.json').write_bytes(original)
            (p / 'plan.json').write_text(json.dumps({'sources': {}, 'render_subtitles': False}), encoding='utf-8')
            result = subprocess.run([sys.executable, str(SKILL / 'scripts/project_memory.py'), 'init', '--project', tmp], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((p / 'channel_profile.json').read_bytes(), original)
            self.assertTrue(json.loads((p / 'plan.json').read_text())['render_subtitles'])

    def test_installer_backs_up_and_preserves_private_local_assets(self):
        spec = importlib.util.spec_from_file_location('public_upgrade', ROOT / 'upgrade_install.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            skills = Path(tmp) / 'skills'
            old = skills / 'explainer-video-pipeline'
            old.mkdir(parents=True)
            files = {'SKILL.md': b'old skill sentinel', 'channel_profile.json': b'{"channel_name":"Own Brand","status":"approved"}', 'private/own-ip.bin': b'own identity sentinel', 'local_environment.json': b'{"sentinel":true}'}
            for rel, raw in files.items():
                p = old / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(raw)
            result = module.install(skills)
            self.assertTrue(result['ok'])
            for rel in ('channel_profile.json', 'private/own-ip.bin', 'local_environment.json'):
                self.assertEqual((old / rel).read_bytes(), files[rel])
            self.assertNotEqual((old / 'SKILL.md').read_bytes(), files['SKILL.md'])
            backups = list((skills.parent / 'skill-backups').glob('explainer-video-pipeline_*'))
            self.assertEqual(len(backups), 1)
            for rel, raw in files.items():
                self.assertEqual((backups[0] / rel).read_bytes(), raw)

if __name__ == '__main__':
    unittest.main()
