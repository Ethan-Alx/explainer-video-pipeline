"""Install the bundled upgrade; back up the old skill and retain local data."""
from pathlib import Path
import argparse, hashlib, json, os, shutil, sys, uuid
from datetime import datetime,timezone

NAME='explainer-video-pipeline'
ROOT=Path(__file__).resolve().parent
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def read(p): return json.loads(p.read_text(encoding='utf-8-sig'))
def within(path,root):
    if not path.resolve().is_relative_to(root.resolve()): raise ValueError('Path outside intended directory: '+str(path))
def configured_profile(path):
    if path.suffix.lower()!='.json': return True
    try:
        p=read(path)
        if not isinstance(p,dict): return True
        status=str(p.get('status',''))
        identity=p.get('identity',{})
        return not ('pending' in status and not p.get('channel_name') and not identity.get('reference_file'))
    except (OSError,ValueError): return True

def install(skills_dir,legacy_name=None):
    manifest=read(ROOT/'MANIFEST.json')
    source=ROOT/NAME
    expected={row['file']:row['sha256'] for row in manifest['skill_files']}
    for rel,digest in expected.items():
        path=source/rel;within(path,source)
        if not path.is_file() or sha(path)!=digest: raise ValueError('Package integrity mismatch: '+rel)
    if legacy_name and (Path(legacy_name).name!=legacy_name or legacy_name.startswith('.') or legacy_name==NAME):
        raise ValueError('--legacy-name must be one exact different skill directory name')
    skills=skills_dir.resolve();skills.mkdir(parents=True,exist_ok=True)
    target=skills/NAME;within(target,skills)
    if target.is_symlink(): raise ValueError('Refuse symlink skill target')
    if legacy_name:
        legacy=skills/legacy_name;within(legacy,skills)
        if legacy.is_symlink(): raise ValueError('Refuse symlink legacy skill')
        if legacy.exists() and not legacy.is_dir(): raise ValueError('Legacy skill path is not a directory')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backups=(skills.parent/'skill-backups').resolve()
    staging_root=(skills.parent/'skill-upgrade-staging').resolve()
    backups.mkdir(parents=True,exist_ok=True);staging_root.mkdir(parents=True,exist_ok=True)
    stage=staging_root/(NAME+'_'+uuid.uuid4().hex);within(stage,staging_root)
    shutil.copytree(source,stage)
    preserved=[]
    if target.exists():
        if not target.is_dir(): raise ValueError('Existing skill target is not a directory')
        for old in target.rglob('*'):
            if not old.is_file() or any(x in ('__pycache__','.git','.pytest_cache') for x in old.relative_to(target).parts) or old.suffix=='.pyc': continue
            rel=old.relative_to(target);within(old,target)
            local_config=old.name in ('channel_profile.json','local_environment.json','project_state.json','decision_log.jsonl')
            custom_profile=rel.parts[:2]==('assets','profiles') and configured_profile(old)
            custom_brand=rel.parts[:2]==('assets','brand') and rel.as_posix() in expected and sha(old)!=expected[rel.as_posix()]
            extra=rel.as_posix() not in expected
            if local_config or custom_profile or custom_brand or extra:
                dest=stage/rel;within(dest,stage);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(old,dest)
                preserved.append(rel.as_posix())
    # All recursive paths are now verified inside their explicitly named roots.
    backup=backups/(NAME+'_'+stamp);within(backup,backups)
    moved_old=False
    if target.exists(): target.rename(backup);moved_old=True
    try:
        stage.rename(target)
    except BaseException:
        if moved_old and not target.exists(): backup.rename(target)
        raise
    for rel,digest in expected.items():
        if rel not in preserved and sha(target/rel)!=digest: raise ValueError('Installed verification failed: '+rel)
    retired=None
    if legacy_name:
        legacy=skills/legacy_name;within(legacy,skills)
        if legacy.is_symlink(): raise ValueError('Refuse symlink legacy skill')
        if legacy.is_dir():
            retired=backups/(legacy_name+'_'+stamp);within(retired,backups)
            legacy.rename(retired)
    receipt={'ok':True,'version':manifest['version'],'installed':str(target),'backup':str(backup) if moved_old else None,
             'legacy_backup':str(retired) if retired else None,'preserved_local_files':preserved,
             'note':'Existing project files, credentials and native editor index were not touched. Restart/open a new Codex chat to discover the upgrade.'}
    report=backups/('upgrade_receipt_'+stamp+'.json')
    report.write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    receipt['receipt']=str(report)
    return receipt

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    default_home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    parser.add_argument('--skills-dir',type=Path,default=default_home/'skills')
    parser.add_argument('--legacy-name',help='Optional exact old skill name to back up outside discovery')
    parser.add_argument('--verify-only',action='store_true')
    args=parser.parse_args()
    try:
        if args.verify_only:
            manifest=read(ROOT/'MANIFEST.json')
            for row in manifest['skill_files']:
                p=ROOT/NAME/row['file'];within(p,ROOT/NAME)
                if not p.is_file() or sha(p)!=row['sha256']: raise ValueError('Package mismatch: '+row['file'])
            result={'ok':True,'version':manifest['version'],'verified_files':len(manifest['skill_files'])}
        else: result=install(args.skills_dir,args.legacy_name)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (OSError,ValueError,KeyError) as exc:
        print(json.dumps({'ok':False,'error':str(exc)},ensure_ascii=False));return 1

if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'): sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
