"""Scoped reuse, actual CJK wrapping and optional video-tool diagnostics."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import draft_engine as e
from make_compat_sample import make
from portability import relocate
from runtime_paths import find_font, font_diagnostics, ffmpeg_diagnostics


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace',required=True,type=Path)
    args=parser.parse_args();work=args.workspace.resolve()
    if work.exists():raise ValueError('Test workspace must be new')
    started=time.perf_counter();work.mkdir(parents=True)
    project=work/'reuse_fixture';make(project,False)
    p=project/'production_plan.ready.json';plan=e.read(p)
    plan['shots'][2]['image']=plan['shots'][0]['image']
    plan['shots'][2]['image_sha256']=plan['shots'][0]['image_sha256']
    plan['shots'][2]['protection']=copy.deepcopy(plan['shots'][0]['protection'])
    e.write(p,plan)
    try:e.validate_plan(p);raise AssertionError('Unexplained repeated still was accepted')
    except ValueError as exc:
        assert 'Repeated still needs' in str(exc)
        unreviewed_error=str(exc)
    source=project/plan['shots'][0]['image']
    review={'status':'reviewed','kind':'return_to_establishing',
            'reason':'Technical fixture: S002 changes state, then S003 deliberately returns to S001 for a recap; no new mechanism state is claimed.',
            'reviewer':'automated_fixture_author (test only, not production art approval)',
            'scope':{'shot_ids':['S001','S003'],'start_us':0,'end_us':12000000,'source_sha256':e.sha(source)},
            'evidence_file':plan['shots'][0]['image'],'evidence_sha256':e.sha(source)}
    plan['shots'][2]['reuse_review']=review;e.write(p,plan)
    validated=e.validate_plan(p)
    assert len(validated['visual_repetition']['reviewed_semantic_reuses'])==1
    assert validated['visual_repetition']['reviewed_semantic_reuses'][0]['new_mechanism_state'] is False
    assert validated['visual_repetition']['actual_state_samples']==0
    built=e.build(p,project/'draft')
    moved=relocate(project/'draft',work/'reused_portable_copy')
    bad=copy.deepcopy(plan);bad['shots'][2]['reuse_review']['evidence_sha256']='0'*64
    e.write(project/'bad_evidence.json',bad)
    try:e.validate_plan(project/'bad_evidence.json');raise AssertionError('Changed evidence was accepted')
    except ValueError as exc:
        assert 'evidence file is missing or changed' in str(exc)
        bad_evidence_error=str(exc)
    chinese=work/'chinese_fixture';make(chinese,False)
    cp=chinese/'production_plan.ready.json';cplan=e.read(cp)
    srt=chinese/cplan['sources']['srt']
    phrase='这是用于中文心理学频道的兼容性测试字幕，需要在不改字词的前提下自动换成两行进行显示。'
    srt.write_text('1\n00:00:00,000 --> 00:00:03,500\n'+phrase+'\n\n2\n00:00:04,000 --> 00:00:07,500\n独立前景与字幕的关系。\n\n3\n00:00:08,000 --> 00:00:11,500\n本声音只用于兼容测试。\n',encoding='utf-8')
    cplan['sources']['srt_sha256']=e.sha(srt);e.write(cp,cplan)
    font=find_font();diagnostics=font_diagnostics(font)
    assert diagnostics['sample_han_glyphs_distinct'],'Set EXPLAINER_VIDEO_FONT to a real CJK font for this explicit CJK test'
    layout=e.subtitle_layout({'number':1,'text':phrase},cplan['subtitle_style'],1920,1080)
    assert len(layout['text'].splitlines())==2
    assert e.canonical_subtitle_text(layout['text'])==e.canonical_subtitle_text(phrase)
    chinese_result=e.build(cp,chinese/'draft')
    assert e.sha(srt)==cplan['sources']['srt_sha256']
    # A controlled missing-tool fixture is distinct from this host's actual discovery.
    with patch.dict(os.environ,{'EXPLAINER_VIDEO_FFMPEG':str(work/'does_not_exist_ffmpeg')},clear=False):
        optional=ffmpeg_diagnostics(False);required=ffmpeg_diagnostics(True)
    assert optional['ok'] and not optional['available'] and not optional['required']
    assert not required['ok'] and required['required'] and not required['available']
    result={'ok':True,'elapsed_seconds':round(time.perf_counter()-started,6),
            'semantic_reuse':{'unreviewed_rejected':unreviewed_error,'evidence_change_rejected':bad_evidence_error,
                              'valid_review':validated['visual_repetition']['reviewed_semantic_reuses'],
                              'build':built,'portable_copy':moved},
            'cjk':{'font':str(font),'diagnostics':diagnostics,'phrase':phrase,'layout':layout,'build':chinese_result,'original_srt_sha256':e.sha(srt)},
            'missing_ffmpeg_fixture':{'optional':optional,'required':required},
            'actual_doctor':e.doctor(),'native_visual_verified':False,'actual_editor_index_touched':False}
    e.write(work/'result.json',result)
    print(json.dumps({'ok':True,'elapsed_seconds':result['elapsed_seconds'],'report':str(work/'result.json'),
                      'two_line_cjk_native_build':True,'semantic_reuse_positive_and_negative':True,
                      'native_visual_verified':False},ensure_ascii=False,indent=2))


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    main()
