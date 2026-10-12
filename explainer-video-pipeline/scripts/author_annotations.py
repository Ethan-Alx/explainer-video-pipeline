"""Compile explicit editorial annotation contracts; never infer semantic targets.

This adapter edits a new candidate only. It neither chooses the layouts nor
approves their visual quality. Original narration, subtitle bytes, cue boundaries,
and main-shot windows are asserted unchanged.
"""
from __future__ import annotations
import argparse, copy, datetime, hashlib, json, math, re
from pathlib import Path
from PIL import Image, ImageDraw
from annotation_geometry import Geometry, glyph_box, pose, nearest_rectangle_edge
from font_coverage import covered_font

ROLES={'object_name','scene_title','stage_caption','semantic_node','review_required'}


def load(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def inherit_parent(child, parent):
    """Same affine parent, not the same numeric dx for different pivots."""
    cb,pb=child['bbox_px'],parent['bbox_px']
    cc=[(cb[0]+cb[2])/2,(cb[1]+cb[3])/2];pc=[(pb[0]+pb[2])/2,(pb[1]+pb[3])/2]
    a,b=child['start_us'],child['end_us']
    times={a,b};times.update(parent['start_us']+k['at_us'] for k in parent.get('animation',{}).get('keyframes',[])
                            if a<parent['start_us']+k['at_us']<b)
    rows=[]
    for t in sorted(times):
        p=pose(parent,t);s=p['scale']
        rows.append({'at_us':t-a,'scale':s,'dx_px':p['dx_px']+(s-1)*(cc[0]-pc[0]),
                     'dy_px':p['dy_px']+(s-1)*(cc[1]-pc[1]),'alpha':1.})
    child['animation']={'entry_us':0,'exit_us':0,'move_y_px':0,'keyframes':rows}
    child['annotation_transform_parent_id']=parent['id']
    return child


def stationary(element, start, end):
    samples=[start,end]
    samples.extend(element['start_us']+k['at_us'] for k in element.get('animation',{}).get('keyframes',[])
                   if start<element['start_us']+k['at_us']<end)
    ps=[pose(element,t) for t in samples]
    return all(max(p[k] for p in ps)-min(p[k] for p in ps)<1e-8 for k in ('scale','dx_px','dy_px'))


def derived_png(image,eid,label,out,kind,parent=None):
    ink=image.getchannel('A').getbbox()
    if not ink:raise ValueError('Empty editorial PNG '+eid)
    dest=out/(eid+'.png');dest.parent.mkdir(parents=True,exist_ok=True);image.crop(ink).save(dest)
    e={'id':eid,'type':'png','asset':str(dest.resolve()),'asset_sha256':digest(dest),'bbox_px':list(ink),
       'start_us':label['start_us'],'end_us':label['end_us'],'layer':label['layer']-1,
       'composition_group':label.get('composition_group'),'purpose':kind+' for explicitly bound editable label '+label['id'],
       'content_fit':'alpha_bbox_contain','long_hold_reason':'Editorial annotation over the same original cue',
       'annotation_component':{'label_id':label['id'],'kind':kind},
       'animation':{'entry_us':0,'exit_us':0,'move_y_px':0,'keyframes':[
           {'at_us':0,'scale':1.,'dx_px':0.,'dy_px':0.,'alpha':1.},
           {'at_us':label['end_us']-label['start_us'],'scale':1.,'dx_px':0.,'dy_px':0.,'alpha':1.}]}}
    if parent:inherit_parent(e,parent)
    return e


def compile_annotations(plan, contract, base, output_dir):
    result=copy.deepcopy(plan);canvas=(plan['canvas']['width'],plan['canvas']['height'])
    if contract.get('canvas_px') and tuple(contract['canvas_px']) != canvas:
        raise ValueError('Annotation contract canvas differs from the plan; reauthor explicit pixel geometry')
    labels=contract.get('labels',[])
    if not isinstance(labels,list):raise ValueError('contract.labels must be a list')
    originals={e['id']:e for e in result.get('mg',[])}
    remove=set(contract.get('remove_element_ids',[]))
    if contract.get('replace_existing_texts',False):remove.update(eid for eid,e in originals.items() if e['type']=='text')
    replaced={q.get('element_id',q.get('id')) for q in labels}
    result['mg']=[e for e in result.get('mg',[]) if e['id'] not in remove|replaced]
    objects={e['id']:e for e in result['mg']}
    for obj in contract.get('objects',[]):
        eid=obj['id']
        if eid in objects:raise ValueError('New contract object duplicates base object: '+eid)
        result['mg'].append(copy.deepcopy(obj));objects[eid]=result['mg'][-1]
    geom=Geometry(result['mg'],base,canvas)
    policies=copy.deepcopy(contract.get('overlap_policies',[]));report=[]
    explicit_pairs={frozenset(p['element_ids']) for p in policies}
    for q in labels:
        eid=q.get('element_id',q.get('id'))
        if not isinstance(eid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',eid):raise ValueError('Unsafe annotation ID')
        old=originals.get(eid,{})
        a=copy.deepcopy(q.get('annotation',{}));role=a.get('role','review_required')
        if role not in ROLES:raise ValueError('Unknown annotation role '+role)
        a['role']=role
        targets=a.get('target_element_ids',[])
        if not isinstance(targets,list) or any(t not in objects for t in targets):raise ValueError('Unknown exact annotation target: '+eid)
        if role=='object_name' and not targets:raise ValueError('Object name requires confirmed target IDs: '+eid)
        placement=a.get('placement','fixed')
        if placement not in ('fixed','follow_target'):raise ValueError('Unknown annotation placement: '+eid)
        primary=a.get('transform_parent_id') or (targets[0] if len(targets)==1 else None)
        if placement=='follow_target' and primary not in targets:
            raise ValueError('Follow annotation needs one explicit transform parent among confirmed targets: '+eid)
        life=a.get('lifecycle',{})
        if life.get('mode') not in ('explicit','target_visible') or not life.get('purpose'):
            raise ValueError('Explicit lifecycle mode and semantic purpose required: '+eid)
        start=q.get('start_us',old.get('start_us'));end=q.get('end_us',old.get('end_us'))
        if life['mode']=='target_visible':
            if not targets:raise ValueError('target_visible requires actual target IDs: '+eid)
            start=max(start,max(objects[t]['start_us'] for t in targets))
            end=min(end,min(objects[t]['end_us'] for t in targets))
        if life.get('settled_at_us') is not None:
            minimum=life['settled_at_us']+life.get('entry_delay_us',0)
            if start<minimum:raise ValueError('Label starts before contracted target settle/delay: '+eid)
        if not isinstance(start,int) or not isinstance(end,int) or start>=end:raise ValueError('Bad annotation timeline: '+eid)
        fontpx=q.get('font_px',q.get('font_px_at_1080',old.get('proxy_font_px')))
        if not isinstance(fontpx,(int,float)) or not 1<=fontpx<=300:raise ValueError('Explicit font_px required: '+eid)
        label={'id':eid,'type':'text','text':q.get('text',old.get('text')),
               'bbox_px':copy.deepcopy(q.get('bbox_px',old.get('bbox_px'))),
               'start_us':start,'end_us':end,'layer':q.get('layer',old.get('layer',60)),
               'composition_group':q.get('composition_group',old.get('composition_group')),
               'purpose':q.get('purpose',life['purpose']),'text_size':fontpx/12,'proxy_font_px':fontpx,
               'text_bold':q.get('weight','bold' if old.get('text_bold',True) else 'regular')=='bold',
               'text_color':q.get('color',q.get('colour',old.get('text_color','#243C45'))),
               'annotation':a,'long_hold_reason':'Independent annotation timing explicitly authored against original cue',
               'native_font_mapping':'Pillow reference font_px/12; target-editor raster remains to be checked',
               'animation':{'entry_us':0,'exit_us':0,'move_y_px':0,'keyframes':[
                   {'at_us':0,'scale':1.,'dx_px':0.,'dy_px':0.,'alpha':1.},
                   {'at_us':end-start,'scale':1.,'dx_px':0.,'dy_px':0.,'alpha':1.}]}}
        for k in ('font_file','text_stroke','text_line_spacing_px'):
            if k in q:label[k]=copy.deepcopy(q[k])
        label['font_file']=covered_font(label['text'],label['text_bold'],label.get('font_file'))
        if placement=='follow_target':inherit_parent(label,objects[primary])
        box,measurement=glyph_box(label)
        a['measured_glyph_bbox_px']=box;a['measurement']=measurement
        a['binding_snapshot']=[{'element_id':t,'asset_sha256':objects[t].get('asset_sha256'),
            'bbox_px':objects[t]['bbox_px'],'start_us':objects[t]['start_us'],'end_us':objects[t]['end_us']}
            for t in targets]
        background=q.get('background')
        plate_box=None
        if background and background.get('kind')!='none':
            if background.get('kind')!='png_plate':
                raise ValueError('Native text background dimensions are not visually calibrated; use explicit png_plate')
            padx,pady=background.get('padding_px',[0,0]);plate_box=[box[0]-padx,box[1]-pady,box[2]+padx,box[3]+pady]
            color=background['color'];alpha=round(255*background.get('alpha',1))
            rgba=tuple(int(color[i:i+2],16) for i in (1,3,5))+(alpha,)
            image=Image.new('RGBA',canvas);ImageDraw.Draw(image).rounded_rectangle(plate_box,
                radius=background.get('round_radius_px',0),fill=rgba)
            plate=derived_png(image,eid+'_bg',label,output_dir/'annotation_assets','local label backing',
                              objects[primary] if placement=='follow_target' else None)
            result['mg'].append(plate)
            policies.append({'element_ids':[eid,plate['id']],'relation':'label_own_plate',
                'reason':'Explicit local backing carries this editable label; no unrelated object waiver'})
            a['plate_element_id']=plate['id'];a['plate_bbox_px']=plate_box
        call=a.get('callout')
        if call:
            target=call.get('target_element_id')
            if target not in targets or objects[target]['type']!='png':raise ValueError('Callout requires confirmed PNG target: '+eid)
            sourcepoint=call.get('source_point_px')
            if not isinstance(sourcepoint,list) or len(sourcepoint)!=2:raise ValueError('Callout needs reviewed original source point: '+eid)
            if placement=='fixed' and not stationary(objects[target],start,end):
                raise ValueError('A fixed label and moving target need independently deforming leader; rigid line is not valid: '+eid)
            if placement=='follow_target' and target!=primary:
                raise ValueError('Leader and label must share their explicit target parent: '+eid)
            anchor=geom.source_point(target,sourcepoint,None if placement=='follow_target' else start)
            edge=nearest_rectangle_edge(anchor,plate_box or box)
            gap=call.get('label_gap_px',0);length=math.dist(anchor,edge)
            if length and gap:edge=[edge[k]+(anchor[k]-edge[k])*gap/length for k in (0,1)]
            image=Image.new('RGBA',canvas);width=round(call.get('width_px',4));draw=ImageDraw.Draw(image)
            draw.line([tuple(anchor),tuple(edge)],fill=call.get('stroke_color','#193241'),width=width)
            radius=call.get('endpoint_dot_px',0)/2
            if radius:draw.ellipse([anchor[0]-radius,anchor[1]-radius,anchor[0]+radius,anchor[1]+radius],
                                   fill=call.get('stroke_color','#193241'))
            leader=derived_png(image,eid+'_lead',label,output_dir/'annotation_assets','scientific callout',
                               objects[primary] if placement=='follow_target' else None)
            leader['annotation_component'].update(target_element_id=target,source_point_px=sourcepoint,
                target_anchor_canvas_px=anchor,label_edge_canvas_px=edge)
            result['mg'].append(leader);a['callout_element_id']=leader['id']
            call.update(mapped_target_anchor_canvas_px=anchor,mapped_label_edge_canvas_px=edge,
                        target_source_sha256=digest(geom.path(objects[target]['asset'])))
            for other,point,relation in [(target,anchor,'leader_target_endpoint'),(eid,edge,'leader_label_edge')]:
                if frozenset((leader['id'],other)) in explicit_pairs:
                    continue  # Keep the authored cutaway corridor or stricter exact-pair rule.
                policies.append({'element_ids':[leader['id'],other],'relation':relation,'endpoint_only':True,
                    'anchor_px':point,'radius_px':max(8,width*2,radius+2),
                    'transform_parent_id':primary if placement=='follow_target' else None,
                    'reason':'Only the specifically reviewed endpoint may touch its associated object/label'})
            if plate_box:
                policies.append({'element_ids':[leader['id'],a['plate_element_id']],'relation':'leader_plate_edge',
                    'endpoint_only':True,'anchor_px':edge,'radius_px':max(8,width*2),
                    'transform_parent_id':primary if placement=='follow_target' else None,
                    'reason':'Callout terminates at its own label backing edge'})
        result['mg'].append(label)
        report.append({'element_id':eid,'role':role,'target_element_ids':targets,'placement':placement,
            'start_us':start,'end_us':end,'glyph_bbox_px':box,'measurement':measurement,
            'callout':copy.deepcopy(call),'visual_review_required':True})
    result['annotation_contract']={'schema_version':'v7.annotation-contract.1',
        'require_explicit_annotation_for_all_texts':contract.get('require_explicit_annotation_for_all_texts',True),
        'overlap_policies':policies,'native_font_render_verified':False,'continuous_geometry_fps':plan['canvas']['fps']}
    ids=[e['id'] for e in result['mg']]
    if len(ids)!=len(set(ids)):raise ValueError('Derived annotation IDs collide with plan')
    if result['sources']!=plan['sources'] or result['canvas']!=plan['canvas'] or result['shots']!=plan['shots']:
        raise ValueError('Annotation author changed locked source, fps, or main windows')
    return result,{'annotations':report,'original_sources_unchanged':True,'main_windows_unchanged':True,
                   'native_font_render_verified':False,'visual_approval_granted':False}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--plan',type=Path,required=True);ap.add_argument('--contract',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True);a=ap.parse_args()
    if a.output.exists():raise ValueError('Candidate exists; preserve earlier bytes')
    from author_paths import resolve_declared_paths
    source=resolve_declared_paths(load(a.plan),a.plan.parent)
    contract=resolve_declared_paths(load(a.contract),a.contract.parent)
    p,report=compile_annotations(source,contract,a.plan.parent,a.output.parent)
    p['production_ready']=False
    p['annotation_authoring_status']='new candidate; actual composite review required'
    p['annotation_contract'].update(source_file=str(a.contract.resolve()),source_sha256=digest(a.contract))
    write(a.output,p);report.update(candidate=str(a.output),candidate_sha256=digest(a.output),
        source_plan=str(a.plan),source_plan_sha256=digest(a.plan),contract=str(a.contract),contract_sha256=digest(a.contract),
        authored_at_utc=datetime.datetime.now(datetime.timezone.utc).isoformat())
    write(a.output.with_suffix('.annotations.json'),report)
    print(json.dumps({'candidate':str(a.output),'sha256':digest(a.output),'labels':len(report['annotations']),
                      'visual_approval_granted':False},ensure_ascii=False))


if __name__=='__main__':main()
