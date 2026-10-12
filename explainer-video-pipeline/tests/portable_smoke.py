"""Exercise the actual CLI without image credits or the user's editor/index."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]


def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def write(p,data):p.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',type=Path,required=True,help='A new test directory; never a real draft library')
    args=parser.parse_args()
    work=args.workspace.resolve()
    if work.exists():raise ValueError('Test workspace exists; choose a new directory')
    work.mkdir(parents=True)
    started=time.perf_counter();results=[]
    def cli(script,*arguments,expect_error=None):
        command=[sys.executable,str(ROOT/'scripts'/script),*map(str,arguments)]
        run=subprocess.run(command,capture_output=True,text=True,encoding='utf-8')
        try:payload=json.loads(run.stdout)
        except json.JSONDecodeError:raise AssertionError('CLI did not return JSON: '+run.stdout+'\n'+run.stderr)
        if expect_error:
            assert run.returncode!=0 and payload.get('ok') is False,(command,payload)
            assert expect_error.lower() in payload['error'].lower(),payload
        else:assert run.returncode==0 and payload.get('ok') is True,(command,payload,run.stderr)
        results.append({'command':command,'exit_code':run.returncode,'expected_rejection':bool(expect_error),'result':payload})
        return payload
    cli('pipeline.py','doctor')
    sample=work/'project'
    cli('make_compat_sample.py','--project',sample,'--build')
    cli('pipeline.py','validate','--draft',sample/'draft','--plan',sample/'production_plan.ready.json')
    moved=work/'relocated_copy'
    cli('pipeline.py','relocate','--draft',sample/'draft','--output',moved)
    incoming=work/'incoming_unchanged_copy'
    shutil.copytree(sample/'draft',incoming)
    # Hide the entire original project during validation. Paths must not work by accident.
    hidden=work/'original_temporarily_hidden'
    assert sample.parent==hidden.parent==work and not hidden.exists()
    sample.rename(hidden)
    try:
        cli('pipeline.py','validate','--draft',moved)
        cli('pipeline.py','relocate','--draft',incoming,'--output',work/'repaired_cross_machine_copy')
        cli('pipeline.py','validate','--draft',work/'repaired_cross_machine_copy')
    finally:hidden.rename(sample)
    content_path=moved/'draft_content.json';original_bytes=content_path.read_bytes()
    content=read(content_path)
    segment=next(seg for track in content['tracks'] for seg in track['segments']
                 if any(k['property_type']=='KFTypePositionX' for k in seg.get('common_keyframes',[])))
    segment['common_keyframes']=[k for k in segment['common_keyframes'] if k['property_type']!='KFTypePositionY']
    write(content_path,content)
    try:cli('pipeline.py','validate','--draft',moved,expect_error='mandatory paired Y')
    finally:content_path.write_bytes(original_bytes)
    plan=read(moved/'plan.snapshot.json')
    asset=moved/plan['shots'][0]['image'];temporarily_hidden=asset.with_name(asset.name+'.negative-test-hidden')
    assert asset.resolve().is_relative_to(work) and not temporarily_hidden.exists()
    asset.rename(temporarily_hidden)
    try:cli('pipeline.py','validate','--draft',moved,expect_error='Main image missing')
    finally:temporarily_hidden.rename(asset)
    # A fake library/index inside this fresh test workspace is the only import target used.
    library=work/'fake_library';library.mkdir()
    index=work/'fake_root_meta_info.json'
    write(index,{'all_draft_store':[{'draft_id':'existing-sentinel','draft_name':'do not change'}],'sentinel_setting':17})
    before=digest(index)
    before_files={p.relative_to(work).as_posix():digest(p) for p in work.rglob('*') if p.is_file()}
    preview=cli('pipeline.py','install','--draft',moved,'--library',library,'--index',index,'--name','Smoke dry-run only','--dry-run')
    after_files={p.relative_to(work).as_posix():digest(p) for p in work.rglob('*') if p.is_file()}
    assert before_files==after_files and before==digest(index) and list(library.iterdir())==[] and preview['files_written']==0
    cli('pipeline.py','build','--plan',sample/'production_plan.ready.json','--output',sample/'draft',expect_error='Output exists')
    cli('pipeline.py','validate','--draft',moved)
    result={'ok':True,'elapsed_seconds':round(time.perf_counter()-started,6),'paid_calls':0,'network_calls':0,
            'native_visual_verified':False,'native_audio_listened':False,'actual_editor_index_touched':False,
            'original_project_hidden_during_relocated_validation':True,
            'dry_run_all_workspace_file_hashes_unchanged':True,'checks':results}
    write(work/'smoke_result.json',result)
    print(json.dumps({'ok':True,'report':str(work/'smoke_result.json'),'elapsed_seconds':result['elapsed_seconds'],
                      'cli_checks':len(results),'expected_rejections':sum(x['expected_rejection'] for x in results),
                      'native_visual_verified':False,'actual_editor_index_touched':False},ensure_ascii=False,indent=2))


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    main()
