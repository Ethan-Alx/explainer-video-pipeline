"""Portable V7 semantics and geometry; synthetic assets, no GUI or network."""
import argparse
import copy
import json
import sys
import time
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import draft_engine as e
from make_compat_sample import make
from author_annotations import compile_annotations
from annotation_validation import diagnose_annotations
from annotation_geometry import Geometry
from font_coverage import covered_font, glyph_codes
from motion_compatibility import require_new_artwork


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--workspace',type=Path,required=True)
    args=ap.parse_args();work=args.workspace.resolve()
    if work.exists():raise ValueError('Test workspace must be new')
    work.mkdir(parents=True);started=time.perf_counter();checks=[]
    assert e.ROOT==ROOT
    make(work/'project',False)
    base=work/'project';plan=e.read(base/'production_plan.ready.json')
    def good(name,p):
        path=base/(name+'.json');e.write(path,p);result=e.validate_plan(path)
        checks.append({'name':name,'passed':result['ok']});return result
    def bad(name,call,match):
        try:call()
        except ValueError as exc:
            assert match in str(exc),(name,str(exc))
            checks.append({'name':name,'passed':True,'expected_rejection':str(exc)})
        else:raise AssertionError('Expected rejection: '+name)
    p=copy.deepcopy(plan);p['canvas']['fps']=24
    good('independent_24fps_no_historical_probe',p)
    p=copy.deepcopy(plan);p['mg']=[];p['sfx']=[];p.pop('annotation_contract')
    good('new_topic_without_annotations_needs_no_annotation_contract',p)
    plain=copy.deepcopy(plan);plain['mg']=[x for x in plain['mg'] if x['type']=='text'];plain['sfx']=[]
    plain['mg'][0].pop('annotation');plain.pop('annotation_contract')
    good('legacy_plain_text_remains_unreviewed_without_opt_in_contract',plain)
    strict=copy.deepcopy(plain);strict['annotation_contract']={'require_explicit_annotation_for_all_texts':True}
    bad('explicit_annotation_contract_still_required',lambda:good('strict_negative',strict),'semantic_target_or_role_not_reviewed')
    assert require_new_artwork(plan,base)['applied'] is False
    guard=copy.deepcopy(plan);guard['artwork_guard']={'rejected_asset_sha256':[e.sha(base/plan['shots'][0]['image'])]}
    bad('declared_rejected_artwork_still_blocked',lambda:good('guard_negative',guard),'Declared rejected artwork SHA')
    guard['artwork_guard']['rejected_asset_sha256']=['0'*64]
    guarded=good('guard_does_not_grant_visual_approval',guard)
    assert guarded['declared_artwork_guard']['visual_approval_granted'] is False
    # A valid opaque, narrow line must remain possible; wide opaque art is rejected.
    img=Image.new('RGBA',(100,100));ImageDraw.Draw(img).ellipse((10,10,90,90),fill='#AD7D39')
    img.save(base/'mg/target.png')
    p=copy.deepcopy(plan);p['sfx']=[];p['mg']=[{'id':'target','type':'png','asset':str(base/'mg/target.png'),
        'asset_sha256':e.sha(base/'mg/target.png'),'bbox_px':[900,200,1100,400],
        'start_us':1000000,'end_us':3500000,'layer':1,'purpose':'technical anchor fixture',
        'content_fit':'alpha_bbox_contain','animation':{'entry_us':0,'exit_us':0,'move_y_px':0}}]
    p.pop('annotation_contract')
    geo=Geometry(p['mg'],base,(1920,1080))
    anchor=geo.source_point('target',[90,50])
    # Horizontal label centre aligns its glyph centre to the actual source anchor.
    contract={'canvas_px':[1920,1080],'labels':[{'element_id':'name','text':'TARGET','font_px':48,
        'bbox_px':[1200,anchor[1]-50,1500,anchor[1]+50],'start_us':1000000,'end_us':3500000,
        'annotation':{'role':'object_name','target_element_ids':['target'],'placement':'fixed',
            'lifecycle':{'mode':'target_visible','purpose':'Explicit source-bound name'},
            'callout':{'target_element_id':'target','source_point_px':[90,50],'width_px':4}}}]}
    candidate,report=compile_annotations(p,contract,base,work/'annotations')
    line=next(x for x in candidate['mg'] if x.get('annotation_component',{}).get('kind')=='scientific callout')
    with Image.open(line['asset']) as im:
        assert min(im.size)<=6 and im.getchannel('A').getextrema()==(255,255)
    good('solid_narrow_callout_accepted_with_explicit_anchor',candidate)
    corrupt=copy.deepcopy(candidate);leader=next(x for x in corrupt['mg'] if x['id']==line['id'])
    opaque=base/'mg/opaque_square.png';Image.new('RGBA',(40,40),'#AD7D39').save(opaque)
    leader.update(asset=str(opaque),asset_sha256=e.sha(opaque))
    bad('opaque_art_does_not_use_narrow_callout_exception',lambda:good('opaque_negative',corrupt),'real alpha transparency')
    bad('transparent_source_anchor_rejected',lambda:geo.source_point('target',[0,0]),'transparent pixels')
    stale=copy.deepcopy(candidate);label=next(x for x in stale['mg'] if x['type']=='text')
    label['end_us']=4000000;label['animation']['keyframes'][-1]['at_us']=3000000
    audit=diagnose_annotations(stale,base,False)
    assert any(x['kind']=='label_active_without_required_target' for x in audit['issues'])
    checks.append({'name':'label_cannot_outlive_semantic_target','passed':True})
    drift=copy.deepcopy(candidate)
    target=next(x for x in drift['mg'] if x['id']=='target')
    target['animation']['keyframes']=[{'at_us':0,'scale':1,'dx_px':0,'dy_px':0,'alpha':1},
                                    {'at_us':2500000,'scale':1,'dx_px':10,'dy_px':0,'alpha':1}]
    label=next(x for x in drift['mg'] if x['type']=='text')
    label['annotation'].update(placement='follow_target',transform_parent_id='target')
    audit=diagnose_annotations(drift,base,False)
    assert any(x['kind']=='relative_parent_transform_drift' for x in audit['issues'])
    checks.append({'name':'label_and_parent_affine_drift_rejected','passed':True})
    collision=copy.deepcopy(plan);collision['annotation_contract']['overlap_policies']=[]
    for element in collision['mg']:
        element.pop('registration_group',None)
        element.pop('preserve_registered_canvas',None)
        if element['type']=='png':element['content_fit']='alpha_bbox_contain'
        element['composition_group']='same_unreviewed_group'
    audit=diagnose_annotations(collision,base)
    assert any(x['kind']=='unapproved_actual_alpha_intersection' for x in audit['issues'])
    checks.append({'name':'legacy_overlap_fields_cannot_replace_explicit_pair_policy','passed':True})
    # Pixel contracts are explicit per canvas, with frame-rate sampling from the plan.
    small=copy.deepcopy(p);small['canvas'].update(width=1280,height=720,fps=24);small['mg']=[]
    small_contract={'canvas_px':[1280,720],'labels':[{'element_id':'title','text':'NEW TOPIC','font_px':40,
        'bbox_px':[400,80,900,160],'start_us':0,'end_us':2000000,
        'annotation':{'role':'scene_title','target_element_ids':[],'placement':'fixed',
            'lifecycle':{'mode':'explicit','purpose':'New topic test heading'}}}]}
    compiled,_=compile_annotations(small,small_contract,base,work/'small')
    audited=diagnose_annotations(compiled,base)
    assert audited['ok'] and compiled['annotation_contract']['continuous_geometry_fps']==24
    checks.append({'name':'explicit_720p_24fps_annotation_contract','passed':True})
    bad('different_contract_canvas_rejected',lambda:compile_annotations(small,contract,base,work/'wrong_canvas'),'canvas differs')
    font=covered_font('中文测试');assert {ord(c) for c in '中文测试'}<=glyph_codes(font)
    checks.append({'name':'actual_cjk_font_cmap_including_ttc','passed':True,'font':font})
    # The CLI output can live outside the source project without broken relative inputs.
    # Include a real mechanism registry, source geometry and hashed evidence image;
    # a file-only rebase would strand the relative state-sample directory.
    target=p['mg'][0]
    layer={k:copy.deepcopy(target[k]) for k in ('bbox_px','layer','start_us','end_us','animation')}
    layer.update(element_id=target['id'],asset_sha256=e.sha(Path(target['asset'])),
                 shape_signature=e.actual_shape_signature(Path(target['asset'])))
    state={'element_id':target['id'],'asset_sha256':layer['asset_sha256'],
           'shape_signature':layer['shape_signature'],'bbox_px':target['bbox_px'],'layer':target['layer'],
           **{k:round(v,6) for k,v in e.mechanism_pose(target,2000000).items()}}
    state.pop('alpha',None)
    geometry=[{k:v for k,v in state.items() if k not in ('element_id','asset_sha256')}]
    evidence_dir=base/'review/mechanisms/state_samples';evidence_dir.mkdir(parents=True)
    evidence=evidence_dir/'fixture.png';evidence.write_bytes(Path(target['asset']).read_bytes())
    registry={'mechanisms':[{'mechanism_id':'technical_fixture','reviewed':True,
        'start_us':1000000,'end_us':3500000,'actual_layers':[layer],
        'state_evidence_samples':[{'reviewed':True,'absolute_at_us':2000000,'objects':[state],
            'geometry_signature':e.json_sha(sorted(geometry,key=e.json_sha)),
            'proxy_file':'historical-location/fixture.png','proxy_sha256':e.sha(evidence)}]}]}
    registry_path=base/'review/mechanisms/fixture_registry.json';e.write(registry_path,registry)
    p['mechanism_registry_file']=registry_path.relative_to(base).as_posix()
    p['mechanism_registry_sha256']=e.sha(registry_path)
    p['mechanism_evidence_directory']=evidence_dir.relative_to(base).as_posix()
    e.write(base/'annotation_input.json',p);e.write(base/'annotation_contract.json',contract)
    output=work/'separate_output/candidate.json'
    command=[sys.executable,str(ROOT/'scripts/author_annotations.py'),'--plan',str(base/'annotation_input.json'),
             '--contract',str(base/'annotation_contract.json'),'--output',str(output)]
    run=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace')
    assert run.returncode==0,(run.stdout,run.stderr)
    result=e.validate_plan(output)
    assert result['ok'] and e.read(output)['production_ready'] is False
    assert Path(e.read(output)['mechanism_evidence_directory'])==evidence_dir
    assert result['visual_repetition']['actual_state_samples']==1
    checks.append({'name':'annotation_cli_rebases_inputs_without_granting_approval','passed':True})
    checks.append({'name':'annotation_cli_keeps_real_registry_evidence_resolvable_in_another_output_directory','passed':True})
    result={'ok':True,'elapsed_seconds':time.perf_counter()-started,'checks':checks,
            'network_calls':0,'paid_calls':0,'gui_used':False,'real_draft_index_touched':False,
            'native_playback_verified':False,'perceptual_audio_verified':False}
    e.write(work/'result.json',result)
    print(json.dumps({'ok':True,'checks':len(checks),'elapsed_seconds':result['elapsed_seconds'],
                      'report':str(work/'result.json')},ensure_ascii=False,indent=2))


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    main()
