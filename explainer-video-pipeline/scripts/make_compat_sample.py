"""Create a 12-second native compatibility fixture; zero network or paid calls."""
import argparse
import json
import math
from pathlib import Path
import struct
import sys
import tempfile
import wave
from PIL import Image, ImageDraw
import draft_engine as e


def tone(path, seconds, amplitude=.025, hz=330):
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        frames=bytearray()
        for i in range(round(seconds*48000)):
            t=i/48000
            envelope=min(1,t/.01,max(0,(seconds-t)/.04))
            frames.extend(struct.pack('<h',round(amplitude*envelope*math.sin(2*math.pi*hz*t)*32767)))
        stream.writeframes(frames)


def make(project, build=False):
    e.configure()
    project=Path(project).resolve()
    project.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='explainer_geometry_sources_', dir=project.parent) as directory:
        raw=Path(directory)
        tone(raw/'test_tone.wav',12,.012,220)
        (raw/'captions.srt').write_text('1\n00:00:00,000 --> 00:00:03,500\nGeometry fixture: paired X and Y.\n\n2\n00:00:04,000 --> 00:00:07,500\nIndependent registered layers.\n\n3\n00:00:08,000 --> 00:00:11,500\nEditable text and short transition.\n',encoding='utf-8')
        (raw/'script.txt').write_text('Technical geometry and quiet synthetic test tones only. Not final artwork, narration, scientific evidence or usable production sound effects.',encoding='utf-8')
        e.init_project(project,'Explainer V7 compatibility fixture',raw/'test_tone.wav',raw/'captions.srt',raw/'script.txt')
    plan=e.read(project/'plan.json')
    plan.update(technical_fixture=True,compatibility_sample=True,
                native_compatibility={'status':'pending_target_machine_playback','native_visual_verified':False})
    for i in range(3):
        image=Image.new('RGB',(1920,1080),'#FFF7E9');d=ImageDraw.Draw(image)
        d.rounded_rectangle((160,220,620,720),radius=50,fill=['#FFD369','#63C6BD','#FF8B68'][i],outline='#203E49',width=12)
        d.ellipse((270+i*50,300,470+i*50,500),fill='#FFFFFF',outline='#203E49',width=10)
        d.rectangle((200,820,1500,832),fill='#FFD369')
        image.save(project/'images'/f'S{i+1:03}.png')
        row={'id':f'S{i+1:03}','image':f'images/S{i+1:03}.png','image_sha256':e.sha(project/'images'/f'S{i+1:03}.png'),
             'start_us':i*4000000,'end_us':(i+1)*4000000,'camera':{'motion':'pan_right','amount':.05} if i==0 else {'motion':'static'},
             'incoming_transition':{'type':'cut','duration_us':0} if i==0 else {'type':'dissolve' if i==1 else 'slide_reveal','duration_us':240000,'direction':'left'},
             'protection':{'reviewed':True,'image_sha256':e.sha(project/'images'/f'S{i+1:03}.png'),'regions':[{'bbox_px':[150,210,710,735],'label':'authored geometric object'}]}}
        plan['shots'].append(row)
    for label,color,bbox in [('outer','#F3B63F',(35,30,270,270)),('inner','#24AAA8',(150,160,250,260))]:
        image=Image.new('RGBA',(300,300));d=ImageDraw.Draw(image)
        d.ellipse(bbox,fill=color,outline='#203E49',width=8)
        image.save(project/'mg'/f'{label}.png')
    for index,label in enumerate(('outer','inner')):
        row={'id':'MG_'+label,'type':'png','asset':f'mg/{label}.png','start_us':1000000+index*500000,'end_us':3500000,'layer':index+1,
             'purpose':'technical shared-canvas registration fixture','bbox_px':[1050,230,1400,580],
             'content_fit':'shared_registered_canvas_no_independent_alpha_fit','preserve_registered_canvas':True,'registration_group':'test_registered',
             'allow_overlap_with':['MG_inner' if label=='outer' else 'MG_outer'],
             'animation':{'entry_us':0,'exit_us':0,'move_y_px':0}}
        plan['mg'].append(row)
    plan['mg'].append({'id':'MG_label','type':'text','text':'REGISTERED LAYERS','text_size':4,'text_color':'#203E49',
                       'start_us':1000000,'end_us':3500000,'layer':3,'purpose':'editable technical label',
                       'bbox_px':[930,650,1530,730],'animation':{'entry_us':120000,'exit_us':0,'move_y_px':5},
                       'annotation':{'role':'stage_caption','target_element_ids':['MG_outer','MG_inner'],
                                     'placement':'fixed','lifecycle':{'mode':'explicit',
                                     'purpose':'Name the paired registration fixture','requires_target_visible':False}}})
    plan['annotation_contract']={'schema_version':'v7.annotation-contract.1',
        'require_explicit_annotation_for_all_texts':True,
        'overlap_policies':[{'element_ids':['MG_outer','MG_inner'],'relation':'registered_fixture_layers',
                            'reason':'The authored inner and outer test circles deliberately share their registration.'}]}
    tone(project/'sfx/test_only.wav',.3,.05,700)
    evidence={'technical_fixture':True,'review_method':'deterministic authored layer starts visibly at 1 second; no subjective auditory claim',
              'element_id':'MG_outer','absolute_at_us':1000000,'native_playback_verified':False}
    e.write(project/'review/tone_event.json',evidence)
    plan['sfx']=[{'id':'FX_test','asset':'sfx/test_only.wav','start_us':1000000,'duration_us':300000,'source_start_us':0,'gain_db':-20,
                  'usage':'synthetic compatibility tone; never adopt as final foley','requires_action_sync':True,
                  'action_sync':{'kind':'mg_event','reviewed':True,'evidence_file':'review/tone_event.json','evidence_sha256':e.sha(project/'review/tone_event.json'),
                                 'element_id':'MG_outer','mg_asset_sha256':e.sha(project/'mg/outer.png'),'keyframe_at_us':0,'event_kind':'entry',
                                 'audio_attack_us':0,'tolerance_us':1000}}]
    e.write(project/'production_plan.ready.json',plan)
    result={'ok':True,'technical_fixture':True,'production_content':False,'paid_calls':0,'network_calls':0,
            'project':str(project),'plan':str(project/'production_plan.ready.json'),'native_visual_verified':False,
            'next':'Open in the target editor and check pan, dissolve, slide, shared layers, subtitles and test tone; record actual observations.'}
    result['plan_validation']=e.validate_plan(project/'production_plan.ready.json')
    if build:result['build']=e.build(project/'production_plan.ready.json',project/'draft')
    (project/'兼容样例说明.txt').write_text('12秒、30fps，三张程序几何图、三条原字幕、两个共享画布透明层、独立原生文字、一次淡出转场、一次滑动转场和合成测试音。没有消费生图额度。所有图与声音仅供兼容验证，不是最终画风、人物IP、科学证据或正式音效。首段有显式配对X/Y。请在目标电脑的剪映保存关闭后按导入说明安装小样，手动实播检查；结构测试通过不等于实播通过。\n',encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',required=True,type=Path)
    parser.add_argument('--build',action='store_true')
    args=parser.parse_args()
    try:
        print(json.dumps(make(args.project,args.build),ensure_ascii=False,indent=2));return 0
    except (ValueError,OSError,ImportError) as exc:
        print(json.dumps({'ok':False,'error':str(exc),'native_visual_verified':False},ensure_ascii=False,indent=2));return 1


if __name__=='__main__':
    if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
