"""A scoped editorial revisit is separate from a new mechanism state."""
from pathlib import Path
import hashlib

KINDS={'detail_cutaway','return_to_establishing','semantic_recap','deliberate_comparison'}


def validate_reuse_review(shot, earlier, plan, base):
    review=shot.get('reuse_review')
    if review is None:return None
    def require(condition,message):
        if not condition:raise ValueError('Invalid reuse_review for '+shot['id']+': '+message)
    def asset(value):
        p=Path(value)
        return p.resolve() if p.is_absolute() else (Path(base)/p).resolve()
    def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
    require(isinstance(review,dict),'must be an object')
    require(review.get('status')=='reviewed','explicit reviewed state required')
    require(review.get('kind') in KINDS,'unknown semantic reuse kind')
    require(all(isinstance(review.get(k),str) and review[k].strip() for k in ('reason','reviewer','evidence_file','evidence_sha256')),
            'nonempty reason, reviewer and hashed actual evidence required')
    scope=review.get('scope',{})
    require(isinstance(scope,dict),'scope must be an object')
    ids=scope.get('shot_ids',[])
    require(isinstance(ids,list) and len(ids)>=2 and all(isinstance(i,str) for i in ids) and len(ids)==len(set(ids)),
            'scope needs at least two distinct shot IDs')
    require(shot['id'] in ids and any(old['id'] in ids for old in earlier),'scope must include this shot and a previous use')
    by_id={s['id']:s for s in plan['shots']}
    require(all(i in by_id for i in ids),'scope contains an unknown shot')
    scoped=[by_id[i] for i in ids]
    require(scope.get('start_us')==min(s['start_us'] for s in scoped)
            and scope.get('end_us')==max(s['end_us'] for s in scoped),'scope time range differs from declared shots')
    require(all(s.get('media_type','image')=='image' for s in scoped),'only reviewed still reuse is covered')
    digest=sha(asset(shot['image']))
    require(scope.get('source_sha256')==digest and all(sha(asset(s['image']))==digest for s in scoped),
            'scope does not identify these exact source-image bytes')
    evidence=asset(review['evidence_file'])
    require(evidence.is_file() and sha(evidence)==review['evidence_sha256'],'actual evidence file is missing or changed')
    return {'shot_id':shot['id'],'kind':review['kind'],'reason':review['reason'],'reviewer':review['reviewer'],
            'scope':scope,'evidence_sha256':review['evidence_sha256'],
            'new_mechanism_state':False,'review_record_checked':True,'visual_semantics_verified_by_tool':False}
