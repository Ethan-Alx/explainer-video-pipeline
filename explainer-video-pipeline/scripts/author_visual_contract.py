"""Realise explicit director states; action prose never becomes guessed motion.

Partial candidates are for actual pixel review, not production-ready approvals.
The root owns backgrounds, object boxes, science anchors, and final acceptance.
"""
from __future__ import annotations
import argparse,copy,hashlib,json,math
from pathlib import Path
from PIL import Image,ImageDraw,ImageChops
from annotation_geometry import Geometry,pose
from author_annotations import compile_annotations,load,write,digest,inherit_parent,derived_png
from annotation_geometry import glyph_box,nearest_rectangle_edge
from png_layout import png_layout_geometry

DEFAULT_PROFILE={'entry':{'duration_us':320000,'scale':[.90,1.],'dy_px':[24,0],'alpha':[0,1]},
                 'reflow':{'duration_us':360000},'exit':{'duration_us':180000},'easing':'ease_out_9_samples'}


def animated_states(element, spec, group, canvas, profile):
    start,end=group['time_window_us'];element['start_us']=start;element['end_us']=end
    element['bbox_px']=copy.deepcopy(spec['bbox_px']);base=element['bbox_px'];center=[(base[0]+base[2])/2,(base[1]+base[3])/2]
    geo=png_layout_geometry(element['asset'],element,canvas)
    fitted=geo['fitted_canvas_bbox_px'] if not geo['independent_alpha_trim_applied'] else geo['visible_bbox_px']
    bw,bh=fitted[2]-fitted[0],fitted[3]-fitted[1]
    name=element['id'].split('__')[-1];events=[];desired_box=base[:];visible=True;alpha=1.
    windows=spec.get('visible_windows_us')
    for i,state in enumerate(group['states']):
        overrides=state.get('object_overrides',{})
        change=overrides.get(element['id'],overrides.get(name,{}))
        desired_box=change.get('bbox_px',desired_box)
        visible=change.get('visible',visible)
        if 'reveal_fraction' in change:alpha=change['reveal_fraction']
        if windows:visible=any(a<=state['at_us']<b for a,b in windows)
        events.append((state['at_us'],desired_box[:],visible,alpha,
                       state.get('transition_us',profile['entry' if i==0 else 'reflow']['duration_us'])
                       if visible else profile['exit']['duration_us']))
    if windows:
        for a,b in windows:
            events.append((a,base[:],True,1.,profile['entry']['duration_us']))
            if b<end:events.append((b,base[:],False,1.,profile['exit']['duration_us']))
    for event in group.get('object_events',[]):
        if event['element_id']==element['id']:
            events.append((event['at_us'],event.get('bbox_px',base),event['event']!='exit',1.,
                           event.get('transition_us',profile['entry']['duration_us'])))
            until=event.get('visible_until_us')
            if until is not None and until<end:
                events.append((until,event.get('bbox_px',base),False,1.,event.get('exit_us',profile['exit']['duration_us'])))
    # Explicit windows govern appearance. State boxes still govern reflow.
    events.sort(key=lambda row:row[0]);combined={}
    for at,box,vis,a,duration in events:
        if at not in combined:combined[at]=[box,vis,a,duration]
        else:
            prior=combined[at]
            if box!=base:prior[0]=box
            prior[1]=vis;prior[2]=a;prior[3]=duration
    def target_for(box,alpha):
        ratio=min((box[2]-box[0])/bw,(box[3]-box[1])/bh)
        return {'scale':ratio,'dx_px':(box[0]+box[2])/2-center[0],
                'dy_px':(box[1]+box[3])/2-center[1],'alpha':alpha}
    keys={0:{'at_us':0,'scale':1.,'dx_px':0.,'dy_px':0.,'alpha':0.}}
    current={'scale':1.,'dx_px':0.,'dy_px':0.,'alpha':0.};settles=[]
    for at,(box,vis,alpha,duration) in sorted(combined.items()):
        at=max(start,at)
        if at>=end:continue
        target=target_for(box,alpha if vis else 0.)
        was_visible=current['alpha']>.001
        if not vis:
            target.update({k:current[k] for k in ('scale','dx_px','dy_px')})
        elif not was_visible:
            duration=profile['entry']['duration_us']
            current={**target,'scale':target['scale']*profile['entry']['scale'][0],
                     'dy_px':target['dy_px']+profile['entry']['dy_px'][0],'alpha':0.}
        if all(abs(target[k]-current[k])<1e-10 for k in target):continue
        duration=min(duration,end-at)
        keys[at-start]={'at_us':at-start,**current}
        for n in range(1,9):
            u=n/8;f=1-(1-u)**3;t=at-start+round(duration*u)
            keys[t]={'at_us':t,**{k:current[k]+(target[k]-current[k])*f for k in target}}
        current=target;settles.append({'event_at_us':at,'settled_at_us':at+duration,'visible':vis,'target_bbox_px':box})
    keys[end-start]={'at_us':end-start,**current}
    element['animation']={'entry_us':0,'exit_us':0,'move_y_px':0,'keyframes':[keys[t] for t in sorted(keys)]}
    element['v7_motion_events']=settles
    return element


def stream_motion(element,event,group,out,target,canvas=(1920,1080)):
    repeat=event.get('repeat_pour')
    if repeat:
        if repeat.get('mode')!='same_asset_staggered_clusters':
            raise ValueError('Unsupported explicit pour repetition mode')
        offsets=repeat['emission_offsets_us']
        if len(offsets)!=repeat['emission_count'] or offsets!=sorted(set(offsets)) or offsets[0]!=0:
            raise ValueError('Pour offsets must be unique, increasing, and begin at zero')
        stages=group.get('material_accumulation',{}).get('stages',[])
        stage=next((s for s in stages if s['stream_element_id']==element['id']),None)
        if repeat.get('keep_each_contact_until_next_mix_settle') and not stage:
            raise ValueError('Repeated pour requires its explicit accumulation settle')
        result=[]
        for index,offset in enumerate(offsets):
            item=copy.deepcopy(element);single=copy.deepcopy(event);single.pop('repeat_pour')
            item['id']=element['id'] if index==0 else element['id']+'_pour_'+str(index+1)
            single['element_id']=item['id'];single['path_timing_us']=[t+offset for t in event['path_timing_us']]
            single['arrival_at_us']=single['path_timing_us'][-1]
            single['repeat_instance']={'logical_element_id':element['id'],'index':index+1,
                'count':len(offsets),'emission_offset_us':offset,'source_asset_sha256':digest(element['asset'])}
            if stage and repeat.get('keep_each_contact_until_next_mix_settle'):
                if single['arrival_at_us']>stage['settled_at_us']:
                    raise ValueError('Repeated contact occurs after contribution settle')
                single['hold_at_arrival_until_us']=stage['settled_at_us']
                single['exit_at_us']=stage['settled_at_us']
            result.extend(stream_motion(item,single,group,out,target,canvas))
        return result
    points=event['path_center_px'];times=event['path_timing_us'];w,h=event['cluster_optical_size_px']
    cx,cy=points[0];element['bbox_px']=[cx-w/2,cy-h/2,cx+w/2,cy+h/2]
    start=times[0];arrival=times[-1];end=arrival
    element['start_us']=start;element['end_us']=end;keys=[]
    for i,(a,b) in enumerate(zip(points,points[1:])):
        for j in range(9):
            if i and not j:continue
            f=j/8;t=times[i]+round((times[i+1]-times[i])*f)
            keys.append({'at_us':t-start,'scale':1.,'dx_px':a[0]+(b[0]-a[0])*f-cx,
                         'dy_px':a[1]+(b[1]-a[1])*f-cy,'alpha':1.})
    element['animation']={'entry_us':0,'exit_us':0,'move_y_px':0,'keyframes':keys}
    element['v7_material_transfer']=copy.deepcopy(event)
    geo=Geometry([element,target],out,canvas);raw=geo.raster(element['id']);last=keys[-1]
    landed=raw.transform(canvas,Image.Transform.AFFINE,(1,0,-last['dx_px'],0,1,-last['dy_px']),
                         Image.Resampling.BILINEAR,fillcolor=(0,0,0,0))
    contact=group['trough_contact_geometry'];points=[geo.source_point(target['id'],p,None) for p in contact['source_inner_cavity_polygon_px']]
    mask=Image.new('L',canvas);ImageDraw.Draw(mask).polygon([tuple(p) for p in points],fill=255)
    landed.putalpha(ImageChops.multiply(landed.getchannel('A'),mask));box=landed.getchannel('A').getbbox()
    if not box:raise ValueError('Actual grain landing is entirely outside actual cavity: '+element['id'])
    asset=out/'material_contacts'/(element['id']+'_landed.png');asset.parent.mkdir(parents=True,exist_ok=True);landed.crop(box).save(asset)
    hold=event.get('hold_at_arrival_until_us',arrival);stop=min(group['time_window_us'][1],hold+event.get('exit_us',180000))
    child=copy.deepcopy(element);child.update(id=element['id']+'_landed',asset=str(asset.resolve()),asset_sha256=digest(asset),
        bbox_px=list(box),start_us=arrival,end_us=stop)
    child.pop('v7_material_transfer',None)
    child['v7_actual_contact']={'source_element_id':element['id'],'source_asset_sha256':element.get('asset_sha256'),
        'target_element_id':target['id'],'actual_canvas_cavity_polygon_px':points,'alpha_clipped_at_contact':True,
        'contact_at_us':arrival,'scope':'authored contact with actual illustrated cavity; not recorded live pouring'}
    if event.get('repeat_instance'):
        child['v7_actual_contact'].update(repeat_instance=copy.deepcopy(event['repeat_instance']),
            retained_until_contribution_settle_us=hold,derived_asset_sha256=digest(asset))
    childkeys=[{'at_us':0,'scale':1.,'alpha':1.,'dx_px':0.,'dy_px':0.}]
    if hold>arrival:childkeys.append({**childkeys[0],'at_us':hold-arrival})
    childkeys.append({**childkeys[0],'at_us':stop-arrival,'alpha':0.})
    child['animation']={'entry_us':0,'exit_us':0,'move_y_px':0,'keyframes':childkeys}
    return [element,child]


def material_accumulation(element,spec,group,out,target,occluder,canvas=(1920,1080)):
    """Reveal disjoint source-space strips, preserving every registered pixel.

    Each strip enters fully opaque. The sum is a sampled monotone spatial mask,
    never a fade of the complete heap; native X/Y/scale remain fixed together.
    """
    if spec['reveal_mode']!='opaque_source_space_cumulative_bottom_up_mask':
        raise ValueError('Unsupported material accumulation reveal')
    source_sha=digest(element['asset'])
    if source_sha!=element['asset_sha256']:raise ValueError('Accumulation source SHA changed')
    with Image.open(element['asset']) as source:raw=source.convert('RGBA')
    if list(raw.size)!=spec['registered_canvas_size_px'] or element['bbox_px']!=spec['registered_bbox_px']:
        raise ValueError('Accumulation must retain its exact registered canvas and box')
    for member in (target,occluder):
        if member['bbox_px']!=element['bbox_px'] or member.get('registration_group')!=element.get('registration_group'):
            raise ValueError('Accumulation, trough, and rim must share registration')
        if png_layout_geometry(member['asset'],member,canvas)['source_canvas_px']!=list(raw.size):
            raise ValueError('Accumulation family source canvas mismatch')
    if not target['layer']<element['layer']<occluder['layer']:
        raise ValueError('Material must sit above trough and below its front rim')
    cavity=Image.new('L',raw.size);ImageDraw.Draw(cavity).polygon(
        [tuple(p) for p in spec['source_inner_cavity_polygon_px']],fill=255)
    eligible=ImageChops.multiply(raw.getchannel('A'),cavity)
    support=eligible.point(lambda a:255 if a else 0)
    row_counts=[support.crop((0,y,raw.width,y+1)).histogram()[255] for y in range(raw.height)]
    total=sum(row_counts)
    if not total:raise ValueError('Accumulation has no actual feed pixels inside cavity')
    # Quantise boundaries to complete source-pixel rows; log the realised pixel
    # fraction so schematic fractions cannot be mistaken for measured mass.
    counts=[0]*(raw.height+1)
    for y in range(raw.height-1,-1,-1):counts[y]=counts[y+1]+row_counts[y]
    stages=spec['stages'];steps=int(spec.get('spatial_mask_steps_per_stage',12))
    if steps<2:raise ValueError('Spatial accumulation needs multiple reveal steps')
    if not stages or spec['starts_empty_until_first_contact_us']!=stages[0]['first_contact_at_us']:
        raise ValueError('Accumulation must begin at its first actual contact')
    result=[];previous_y=raw.height;previous_fraction=0.;previous_settle=group['time_window_us'][0]
    for stage_index,stage in enumerate(stages):
        fraction=stage['cumulative_spatial_reveal_fraction'];first=stage['first_contact_at_us'];settle=stage['settled_at_us']
        if not previous_fraction<fraction<=1 or not previous_settle<=first<settle<=spec['final_hold_until_us']:
            raise ValueError('Accumulation stages must increase and follow their contact times')
        transfer=next(e for e in group['material_transfer_events'] if e['element_id']==stage['stream_element_id'])
        arrivals=[transfer['path_timing_us'][-1]+o for o in transfer.get('repeat_pour',{}).get('emission_offsets_us',[0])]
        if first!=min(arrivals) or stage['last_contact_at_us']!=max(arrivals) or max(arrivals)>settle:
            raise ValueError('Accumulation contacts differ from the actual repeated stream timing')
        for step in range(1,steps+1):
            requested=previous_fraction+(fraction-previous_fraction)*step/steps
            boundary=min(range(previous_y+1),key=lambda y:abs(counts[y]-requested*total))
            if boundary==previous_y:continue
            strip=raw.copy();alpha=Image.new('L',raw.size)
            alpha.paste(eligible.crop((0,boundary,raw.width,previous_y)),(0,boundary));strip.putalpha(alpha)
            if not alpha.getbbox():continue
            index=len(result)+1;eid=element['id'] if index==1 else element['id']+'_spatial_'+str(index).zfill(2)
            asset=out/'material_accumulation'/(eid+'.png');asset.parent.mkdir(parents=True,exist_ok=True);strip.save(asset)
            at=first+round((settle-first)*(step-1)/(steps-1));end=stage['keep_material_visible_until_us']
            if end!=spec['final_hold_until_us']:raise ValueError('Cumulative material must survive all later contributions')
            child=copy.deepcopy(element);child.pop('v7_motion_events',None)
            child.update(id=eid,asset=str(asset.resolve()),asset_sha256=digest(asset),start_us=at,end_us=end,
                content_fit='shared_registered_canvas_no_independent_alpha_fit',preserve_registered_canvas=True)
            fixed={'scale':1.,'dx_px':0.,'dy_px':0.,'alpha':1.}
            child['animation']={'entry_us':0,'exit_us':0,'move_y_px':0,
                'keyframes':[{'at_us':0,**fixed},{'at_us':end-at,**fixed}]}
            child['v7_material_accumulation']={'source_element_id':element['id'],
                'source_file':element['asset'],'source_asset_sha256':source_sha,'derived_asset_sha256':digest(asset),
                'target_element_id':target['id'],'occluder_element_id':occluder['id'],
                'registered_canvas_size_px':list(raw.size),'registered_bbox_px':element['bbox_px'][:],
                'source_inner_cavity_polygon_px':spec['source_inner_cavity_polygon_px'],
                'source_strip_y_px':[boundary,previous_y],'stage_index':stage_index+1,
                'stream_element_id':stage['stream_element_id'],'first_contact_at_us':first,
                'last_contact_at_us':stage['last_contact_at_us'],'settled_at_us':settle,'reveal_at_us':at,
                'requested_cumulative_spatial_fraction':requested,'actual_cumulative_source_pixel_fraction':counts[boundary]/total,
                'eligible_source_pixel_count':total,'strip_source_pixel_count':counts[boundary]-counts[previous_y],
                'source_pixels_unchanged_inside_strip':True,'independent_trim_scale_or_translation':False,
                'reveal_kind':'opaque_disjoint_bottom_up_source_strip',
                'temporal_sampling_max_us':math.ceil((settle-first)/(steps-1)),
                'scope':'sampled spatial accumulation of existing artwork; schematic, not ingredient mass or measured fill'}
            result.append(child);previous_y=boundary
        previous_fraction=fraction;previous_settle=settle
    if previous_fraction!=1. or counts[previous_y]!=total:raise ValueError('Final material must include all eligible source pixels')
    return result


def progressive_route(spec,main,out,canvas,group):
    with Image.open(main['image']) as source:sw,sh=source.size
    scale=max(canvas[0]/sw,canvas[1]/sh);ox=(canvas[0]-sw*scale)/2;oy=(canvas[1]-sh*scale)/2
    points=[[ox+x*scale,oy+y*scale] for x,y in spec['source_points_px']]
    start,settled,end=spec['start_us'],spec['settled_at_us'],spec['end_us']
    count=len(points)-1;rows=[]
    for i in range(1,len(points)):
        a=start+round((settled-start)*(i-1)/count);b=end if i==count else start+round((settled-start)*i/count)
        image=Image.new('RGBA',canvas);draw=ImageDraw.Draw(image)
        if spec.get('outline'):
            draw.line([tuple(p) for p in points[:i+1]],fill=spec['outline']['color'],width=spec['outline']['total_width_px'],joint='curve')
        draw.line([tuple(p) for p in points[:i+1]],fill=spec['color'],width=spec['stroke_px'],joint='curve')
        arrow=spec.get('arrowhead')
        if arrow and i==count:
            tip=points[i];prev=points[i-1];length=math.dist(tip,prev)
            if length:
                ux,uy=(tip[0]-prev[0])/length,(tip[1]-prev[1])/length
                al=arrow.get('length_px',26);aw=arrow.get('width_px',25)
                back=[tip[0]-ux*al,tip[1]-uy*al]
                draw.polygon([tuple(tip),(back[0]-uy*aw/2,back[1]+ux*aw/2),
                              (back[0]+uy*aw/2,back[1]-ux*aw/2)],fill=spec['color'])
        box=image.getchannel('A').getbbox();eid=spec['element_id']+'_p'+str(i)
        asset=out/'scientific_routes'/(eid+'.png');asset.parent.mkdir(parents=True,exist_ok=True);image.crop(box).save(asset)
        rows.append({'id':eid,'type':'png','asset':str(asset.resolve()),'asset_sha256':digest(asset),
            'bbox_px':list(box),'start_us':a,'end_us':b,'layer':30,'composition_group':group['mechanism_id'],
            'content_fit':'alpha_bbox_contain','purpose':spec['meaning'],
            'long_hold_reason':'Cumulative schematic prefix remains visible over the same original explanation',
            'animation':{'entry_us':0,'exit_us':0,'move_y_px':0,'keyframes':[{'at_us':0,'alpha':1}, {'at_us':b-a,'alpha':1}]},
            'v7_route':{'logical_id':spec['element_id'],'source_points_px':spec['source_points_px'],
                'source_file':main['image'],'source_sha256':digest(main['image']),'source_cover':{'scale':scale,'offset_px':[ox,oy]},
                'canvas_prefix_points_px':points[:i+1],'scientific_status':'explicit source-position teaching annotation, not measured flow'}})
    return rows


def registered_relations(group, byid, current, out, canvas):
    """Preserve reviewed source geometry with one source-to-parent affine map.

    Includes cumulative path pixels and destination points, not inferred lines.
    Source child windows remain the original narration windows.
    """
    result=[];policies=[];current_byid={e['id']:e for e in current}
    for relation in group.get('registered_relationship_groups',[]):
        parent_id=relation.get('parent_element_id',relation['element_ids'][0])
        parent=current_byid[parent_id];old_parent=byid[parent_id]
        old_geo=Geometry([old_parent],out,canvas);new_geo=Geometry([parent],out,canvas)
        old_box=old_geo.ink_box(parent_id);new_box=new_geo.ink_box(parent_id)
        ratios=[(new_box[k+2]-new_box[k])/(old_box[k+2]-old_box[k]) for k in (0,1)]
        if abs(ratios[0]-ratios[1])>.008:raise ValueError('Registered relation cannot distort source '+parent_id)
        factor=sum(ratios)/2;offset=[new_box[k]-factor*old_box[k] for k in (0,1)]
        children=list(dict.fromkeys(relation['element_ids']+relation.get('include_source_children',[])))
        for eid in children:
            if eid==parent_id:continue
            if eid not in byid:raise ValueError('Missing explicit source relation child '+eid)
            old=byid[eid];child=copy.deepcopy(old)
            child.pop('allow_overlap_with',None);child.pop('registration_group',None)
            child['bbox_px']=[old['bbox_px'][k]*factor+offset[k%2] for k in range(4)]
            child['content_fit']='alpha_bbox_contain'
            start,end=child['start_us'],child['end_us'];b=child['bbox_px'];cc=[(b[0]+b[2])/2,(b[1]+b[3])/2]
            pb=parent['bbox_px'];pc=[(pb[0]+pb[2])/2,(pb[1]+pb[3])/2]
            times={start,end}
            times.update(parent['start_us']+x['at_us'] for x in parent['animation']['keyframes'] if start<parent['start_us']+x['at_us']<end)
            times.update(old['start_us']+x['at_us'] for x in old.get('animation',{}).get('keyframes',[]) if start<old['start_us']+x['at_us']<end)
            keys=[]
            for t in sorted(times):
                pp=pose(parent,t);cp=pose(old,t)
                keys.append({'at_us':t-start,'scale':pp['scale']*cp['scale'],
                    'dx_px':pp['dx_px']+(pp['scale']-1)*(cc[0]-pc[0])+cp['dx_px']*factor*pp['scale'],
                    'dy_px':pp['dy_px']+(pp['scale']-1)*(cc[1]-pc[1])+cp['dy_px']*factor*pp['scale'],
                    'alpha':pp['alpha']*cp['alpha']})
            child['animation']={'keyframes':keys,'entry_us':0,'exit_us':0,'move_y_px':0}
            child['v7_registered_relationship']={'parent_element_id':parent_id,'source_parent_bbox':old_box,
                'new_parent_bbox':new_box,'uniform_scale':factor,'offset_px':offset,
                'source_child_bbox':old['bbox_px'],'source_child_sha256':old['asset_sha256'],
                'scope':'original scientific path/contact geometry, common affine remap and narration timing'}
            result.append(child)
            policies.append({'element_ids':[eid,parent_id],'relation':'reviewed_source_registered_relation',
                'reason':'Specific child pixels are registered to actual parent source through one documented affine map; no text waiver.'})
        # Replace authored optical slots of explicitly related child objects.
        replaces={e['id'] for e in result}
        current[:]=[e for e in current if e['id'] not in replaces]
    return result,policies


def semantic_connections(groups,plan,out):
    canvas=(plan['canvas']['width'],plan['canvas']['height'])
    byid={e['id']:e for e in plan['mg']};policies=[]
    for group in groups:
        for spec in group.get('semantic_connections',[]):
            a,b=byid[spec['from_label_id']],byid[spec['to_label_id']]
            ga,_=glyph_box(a);gb,_=glyph_box(b)
            ac=[(ga[0]+ga[2])/2,(ga[1]+ga[3])/2];bc=[(gb[0]+gb[2])/2,(gb[1]+gb[3])/2]
            pa=nearest_rectangle_edge(bc,ga);pb=nearest_rectangle_edge(ac,gb)
            length=math.dist(pa,pb);gap=spec.get('label_gap_px',16)
            if length<=2*gap+spec.get('arrow_length_px',22):raise ValueError('Semantic label edge space insufficient')
            ux,uy=(pb[0]-pa[0])/length,(pb[1]-pa[1])/length
            pa=[pa[0]+ux*gap,pa[1]+uy*gap];pb=[pb[0]-ux*gap,pb[1]-uy*gap]
            im=Image.new('RGBA',canvas);d=ImageDraw.Draw(im);color=spec.get('color','#267FA0')
            d.line([tuple(pa),tuple(pb)],fill=color,width=spec.get('stroke_px',8))
            al,aw=spec.get('arrow_length_px',22),spec.get('arrow_width_px',20)
            back=[pb[0]-ux*al,pb[1]-uy*al]
            d.polygon([tuple(pb),(back[0]-uy*aw/2,back[1]+ux*aw/2),(back[0]+uy*aw/2,back[1]-ux*aw/2)],fill=color)
            label={**a,'start_us':spec['start_us'],'end_us':spec['end_us']}
            if not max(a['start_us'],b['start_us'])<=label['start_us']<label['end_us']<=min(a['end_us'],b['end_us']):
                raise ValueError('Semantic connector exceeds its two word lifetimes')
            element=derived_png(im,spec.get('element_id',group['mechanism_id']+'__semantic_relation'),label,out/'semantic_relations','explicit semantic relation')
            element.pop('annotation_component',None)
            element['v7_semantic_connection']={'from_label_id':a['id'],'to_label_id':b['id'],'actual_word_edge_points_px':[pa,pb],
                'meaning':spec.get('meaning','authored semantic relation, not a specimen or measured path')}
            plan['mg'].append(element)
    return policies


def author(plan,contract,backgrounds,out,only=None):
    canvas=(plan['canvas']['width'],plan['canvas']['height'])
    if contract.get('canvas_px') and tuple(contract['canvas_px']) != canvas:
        raise ValueError('Visual contract canvas differs from plan; explicit geometry must be reauthored')
    p=copy.deepcopy(plan);byid={e['id']:e for e in p['mg']};flat={'labels':[],'remove_element_ids':[],
        'overlap_policies':copy.deepcopy(contract.get('overlap_policies',[])),
        'require_explicit_annotation_for_all_texts':not only};groups=[];motion=contract.get('shared_rules',{}).get('motion_profile',DEFAULT_PROFILE)
    for group in contract['mechanisms']:
        if only and group['scene_id'] not in only:continue
        groups.append(group);gid=group['mechanism_id'];a,b=group['time_window_us']
        old=[e for e in p['mg'] if e.get('composition_group')==gid]
        p['mg']=[e for e in p['mg'] if e.get('composition_group')!=gid]
        main=next(s for s in p['shots'] if s['start_us']==a and s['end_us']==b)
        bg=backgrounds.get('mapping',{}).get(gid)
        if bg:
            if digest(bg['image'])!=bg['image_sha256']:raise ValueError('Actual approved background changed: '+gid)
            main.update(image=bg['image'],image_sha256=bg['image_sha256'],media_type='image')
            c=bg['camera'];main['camera']={'motion':'static','amount':0,'focus':[.5,.5],
                'keyframes':[{'at_us':0,**c},{'at_us':b-a,**c}]}
            main['protection']={'reviewed':False,'image_sha256':bg['image_sha256'],'regions':[],
                'review_note':'Root selected actual background; composed sample review pending'}
        if main.get('camera',{}).get('keyframes') and any(len({x[k] for x in main['camera']['keyframes']})>1 for k in ('scale','x','y')) and group.get('native_scientific_annotations'):
            raise ValueError('Source-position routes require explicit shared camera transform; do not map against moving background')
        transfers={x['element_id']:x for x in group.get('material_transfer_events',[])}
        for spec in group['objects']:
            eid=spec['element_id']
            if eid not in byid:
                if eid.endswith('_actual_base'):continue
                raise ValueError('Contract object has no actual bound source: '+eid)
            obj=copy.deepcopy(byid[eid]);obj.pop('allow_overlap_with',None)
            if spec['bbox_semantics']=='registered_master_canvas':
                obj['content_fit']='shared_registered_canvas_no_independent_alpha_fit';obj['preserve_registered_canvas']=True
                registration=next((r for r in group.get('registered_groups',[]) if eid in r['element_ids']),None)
                if registration:obj['registration_group']=registration['id']
                if not obj.get('registration_group'):raise ValueError('Registered object lacks explicit family: '+eid)
            else:
                obj.pop('registration_group',None);obj.pop('preserve_registered_canvas',None);obj['content_fit']='alpha_bbox_contain'
            animated_states(obj,spec,group,canvas,motion)
            if eid in transfers:
                target=next(x for x in p['mg'] if x['id']==transfers[eid]['target_element_id'])
                p['mg'].extend(stream_motion(obj,transfers[eid],group,out,target,canvas))
            else:p['mg'].append(obj)
        current=[e for e in p['mg'] if e.get('composition_group')==gid]
        if group.get('material_accumulation'):
            spec=group['material_accumulation'];members={e['id']:e for e in current}
            source=members[spec['source_element_id']]
            strips=material_accumulation(source,spec,group,out,members[spec['target_element_id']],members[spec['occluder_element_id']],canvas)
            current=[e for e in current if e['id']!=source['id']]+strips
        extra,policies=registered_relations(group,byid,current,out,canvas)
        p['mg']=[e for e in p['mg'] if e.get('composition_group')!=gid]+current+extra
        flat['overlap_policies'].extend(policies)
        aliases={}
        for annotation in group.get('native_scientific_annotations',[]):
            actual=progressive_route(annotation,main,out,canvas,group)
            p['mg'].extend(actual);aliases[annotation['element_id']]=[r['id'] for r in actual]
        for rawlabel in group['labels']:
            label=copy.deepcopy(rawlabel);label['composition_group']=gid
            targets=label['annotation'].get('target_element_ids',[])
            if any(t in aliases for t in targets):
                label['annotation']['semantic_target_groups']=copy.deepcopy(targets)
                label['annotation']['target_element_ids']=[item for t in targets for item in aliases.get(t,[t])]
                label['annotation']['lifecycle']['target_visibility']='any'
            flat['labels'].append(label)
    # Old sounds cannot be silently retimed to newly authored object events.
    p['sfx']=[]
    p['production_ready']=False
    p['v7_scope']={'selected_mechanism_ids':[g['mechanism_id'] for g in groups],
        'partial_visual_candidate':bool(only),'native_render_verified':False,'motion_profile':motion,
        'source_audio_and_subtitles_unchanged':True,'old_SFX_adopted':0}
    p,report=compile_annotations(p,flat,out,out)
    semantic_connections(groups,p,out)
    p['v7_state_review_schedule']=[{'scene_id':g['scene_id'],'mechanism_id':g['mechanism_id'],
        'time_window_us':g['time_window_us'],'states':copy.deepcopy(g['states'])} for g in groups]
    for group,schedule in zip(groups,p['v7_state_review_schedule']):
        for stage in group.get('material_accumulation',{}).get('stages',[]):
            schedule['states'].append({'name':'spatial accumulation settled: '+stage['stream_element_id'],
                'at_us':stage['first_contact_at_us'],'settled_at_us':stage['settled_at_us'],
                'last_contact_at_us':stage['last_contact_at_us'],
                'cumulative_spatial_reveal_fraction':stage['cumulative_spatial_reveal_fraction'],
                'review_scope':'actual registered material after contribution settle; repeated path samples cover all contacts'})
        schedule['states'].sort(key=lambda s:s['at_us'])
    return p,report


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--plan',type=Path,required=True);ap.add_argument('--contract',type=Path,required=True)
    ap.add_argument('--backgrounds',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);ap.add_argument('--only',nargs='*')
    args=ap.parse_args()
    if args.output.exists():raise ValueError('Preserve existing candidate')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    from author_paths import resolve_declared_paths
    original=resolve_declared_paths(load(args.plan),args.plan.parent)
    contract=resolve_declared_paths(load(args.contract),args.contract.parent)
    bg=resolve_declared_paths(load(args.backgrounds),args.backgrounds.parent)
    # Snapshot the mutable upstream contract before any dependent operation.
    frozen=args.output.parent/'visual_contract.input.json';frozen.write_bytes(args.contract.read_bytes())
    candidate,report=author(original,contract,bg,args.output.parent,set(args.only) if args.only else None)
    candidate['annotation_contract'].update(source_file=str(frozen.resolve()),source_sha256=digest(frozen))
    candidate['v7_input_provenance']=[{'file_path':str(f),'sha256':digest(f)} for f in (args.plan,frozen,args.backgrounds)]
    if candidate['sources']!=original['sources'] or candidate['canvas']!=original['canvas']:
        raise ValueError('Author must not change original MP3/SRT/canvas')
    if [(s['id'],s['start_us'],s['end_us']) for s in candidate['shots']]!=[(s['id'],s['start_us'],s['end_us']) for s in original['shots']]:
        raise ValueError('Main semantic windows changed')
    write(args.output,candidate);write(args.output.with_suffix('.author.json'),report)
    print(json.dumps({'candidate':str(args.output),'sha256':digest(args.output),'mechanisms':len(candidate['v7_state_review_schedule']),
        'mg':len(candidate['mg']),'visual_review_pending':True},ensure_ascii=False))


if __name__=='__main__':main()
