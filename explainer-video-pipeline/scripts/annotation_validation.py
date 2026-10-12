"""Contract-specific red checks; numeric approval is not visual/native approval."""
from __future__ import annotations
import math
from annotation_geometry import Geometry, pose, point_after_pose, glyph_box, timeline_samples, collision_samples


def diagnose_annotations(plan, base, check_collisions=True):
    mg=plan.get('mg',[]);byid={e['id']:e for e in mg};canvas=(plan['canvas']['width'],plan['canvas']['height'])
    g=Geometry(mg,base,canvas);issues=[];rows=[];cfg=plan.get('annotation_contract',{})
    fps=plan['canvas']['fps']
    subtitle_y=plan.get('subtitle_style',{}).get('y',-.82)
    subtitle_top=(canvas[1]*min(.84,(1-subtitle_y)/2-.07)
                  if plan.get('render_subtitles', True) else canvas[1]+.5)
    def issue(eid,kind,**data):issues.append({'element_id':eid,'kind':kind,**data})
    for e in mg:
        if e['type'] not in ('png','text'):continue
        bounds=[g.ink_box(e['id'],t) for t in [e['start_us'],e['end_us']]+[
            e['start_us']+p['at_us'] for p in e.get('animation',{}).get('keyframes',[])]]
        envelope=[min(b[0] for b in bounds),min(b[1] for b in bounds),max(b[2] for b in bounds),max(b[3] for b in bounds)]
        if envelope[0]<-.5 or envelope[1]<-.5 or envelope[2]>canvas[0]+.5 or envelope[3]>=subtitle_top:
            issue(e['id'],'actual_ink_outside_frame_or_subtitle_guard',envelope_px=envelope)
        if e['type']!='text':continue
        a=e.get('annotation')
        if not a:
            if cfg.get('require_explicit_annotation_for_all_texts',bool(cfg)):issue(e['id'],'semantic_target_or_role_not_reviewed')
            rows.append({'element_id':e['id'],'role':'review_required','target_element_ids':[]});continue
        role=a.get('role');targets=a.get('target_element_ids',[]);life=a.get('lifecycle',{});placement=a.get('placement')
        if role not in ('object_name','scene_title','stage_caption','semantic_node'):
            issue(e['id'],'semantic_target_or_role_not_reviewed')
        if role=='object_name' and not targets:issue(e['id'],'object_name_has_no_confirmed_target')
        if any(t not in byid for t in targets):
            issue(e['id'],'confirmed_target_missing');continue
        if life.get('mode') not in ('explicit','target_visible') or not life.get('purpose'):
            issue(e['id'],'lifecycle_intent_missing')
        box,measurement=glyph_box(e)
        row={'element_id':e['id'],'role':role,'target_element_ids':targets,'placement':placement,
             'glyph_bbox_px':box,'measurement':measurement,'max_parent_affine_error_px':0,
             'label_time_us':[e['start_us'],e['end_us']]}
        if life.get('settled_at_us') is not None and e['start_us']<life['settled_at_us']+life.get('entry_delay_us',0):
            issue(e['id'],'label_before_contracted_settle')
        must_visible=life.get('requires_target_visible',role=='object_name' or life.get('mode')=='target_visible')
        if must_visible:
            bad=[]
            if life.get('target_visibility')=='any':
                for at in timeline_samples(e['start_us'],e['end_us'],[e]+[byid[t] for t in targets],fps):
                    if not any(byid[t]['start_us']<=at<byid[t]['end_us'] and pose(byid[t],at)['alpha']>=life.get('visible_alpha_min',.5) for t in targets):
                        bad.append({'target_element_ids':targets,'first_us':at});break
            for target in ([] if life.get('target_visibility')=='any' else targets):
                t=byid[target]
                for at in timeline_samples(e['start_us'],e['end_us'],[e,t],fps):
                    if not t['start_us']<=at<t['end_us'] or pose(t,at)['alpha']<life.get('visible_alpha_min',.5):
                        bad.append({'target_element_id':target,'first_us':at});break
            if bad:issue(e['id'],'label_active_without_required_target',targets=bad)
        if placement=='follow_target':
            parent=a.get('transform_parent_id') or (targets[0] if len(targets)==1 else None)
            if parent not in targets:
                issue(e['id'],'follow_target_parent_ambiguous')
            else:
                b=e['bbox_px'];center=[(b[0]+b[2])/2,(b[1]+b[3])/2];max_error=0
                for at in timeline_samples(e['start_us'],e['end_us'],[e,byid[parent]],fps):
                    expected=point_after_pose(center,byid[parent],at);actual=point_after_pose(center,e,at)
                    max_error=max(max_error,math.dist(expected,actual),
                                  abs(pose(e,at)['scale']-pose(byid[parent],at)['scale'])*max(canvas))
                row['max_parent_affine_error_px']=max_error
                if max_error>1e-5:issue(e['id'],'relative_parent_transform_drift',max_error_px=max_error)
                for component in ('plate_element_id','callout_element_id'):
                    child=byid.get(a.get(component))
                    if not child:continue
                    if child['start_us']!=e['start_us'] or child['end_us']!=e['end_us']:
                        issue(e['id'],'annotation_component_lifecycle_mismatch',component=child['id'])
                    b=child['bbox_px'];cc=[(b[0]+b[2])/2,(b[1]+b[3])/2]
                    error=max(math.dist(point_after_pose(cc,byid[parent],at),point_after_pose(cc,child,at))
                              for at in timeline_samples(e['start_us'],e['end_us'],[child,byid[parent]],fps))
                    if error>1e-5:issue(e['id'],'annotation_component_parent_drift',component=child['id'],max_error_px=error)
        elif placement!='fixed':issue(e['id'],'placement_intent_missing')
        if a.get('callout'):
            c=a['callout'];target=c.get('target_element_id')
            try:
                expected=g.source_point(target,c['source_point_px'],None if placement=='follow_target' else e['start_us'])
                error=math.dist(expected,c['mapped_target_anchor_canvas_px'])
                row['callout_source_mapping_error_px']=error
                if error>1e-5:issue(e['id'],'callout_source_anchor_mapping_changed',error_px=error)
                leader=byid[a['callout_element_id']]
                if leader['start_us']!=e['start_us'] or leader['end_us']!=e['end_us']:
                    issue(e['id'],'callout_lifetime_differs_from_label')
            except (ValueError,KeyError,TypeError) as exc:
                issue(e['id'],'callout_anchor_invalid',detail=str(exc))
        rows.append(row)
    collisions=collision_samples(mg,base,canvas,fps,cfg.get('overlap_policies',[])) if check_collisions else None
    if collisions:
        issues.extend({'element_id':r['element_ids'][0],'kind':'unapproved_actual_alpha_intersection',**r}
                      for r in collisions['unapproved_intersections'])
    return {'ok':not issues,'issues':issues,'annotations':rows,'collisions':collisions,
        'native_font_render_verified':False,'native_full_film_visual_verified':False,
        'scope':'explicit editorial semantic/timing/parent constraints and actual alpha/glyph numeric checks; requires visual review'}


def require_annotation_contract(plan,base):
    result=diagnose_annotations(plan,base)
    if not result['ok']:
        first=result['issues'][0]
        raise ValueError('V7 annotation constraint: '+first['element_id']+' / '+first['kind']+
                         ' ('+str(len(result['issues']))+' total; run annotation audit for details)')
    return result
