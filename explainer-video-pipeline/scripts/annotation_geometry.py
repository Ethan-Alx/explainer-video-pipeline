"""Actual-alpha and measured-glyph annotation geometry.

Pillow font measurement is a reproducible proxy, not proof of Jianying glyph
rasterisation. Source points always refer to the original RGBA canvas, before
alpha trimming, contain resize, or editable native transforms.
"""
from __future__ import annotations
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from png_layout import prepare_png_canvas
from font_coverage import covered_font


def pose(element, absolute_us):
    keys = element.get('animation', {}).get('keyframes', [])
    default = {'scale': 1., 'dx_px': 0., 'dy_px': 0., 'alpha': 1.}
    if not keys:
        return default
    t = max(0, min(absolute_us-element['start_us'], element['end_us']-element['start_us']))
    if t <= keys[0]['at_us']:
        return {k: keys[0].get(k, v) for k, v in default.items()}
    for a, b in zip(keys, keys[1:]):
        if a['at_us'] <= t <= b['at_us']:
            f = (t-a['at_us'])/(b['at_us']-a['at_us'])
            return {k: a.get(k, v)+(b.get(k, v)-a.get(k, v))*f for k, v in default.items()}
    return {k: keys[-1].get(k, v) for k, v in default.items()}


def point_after_pose(point, element, absolute_us):
    p = pose(element, absolute_us)
    b = element['bbox_px']; cx, cy = (b[0]+b[2])/2, (b[1]+b[3])/2
    return [cx+(point[0]-cx)*p['scale']+p['dx_px'],
            cy+(point[1]-cy)*p['scale']+p['dy_px']]


def box_after_pose(box, element, absolute_us):
    a = point_after_pose(box[:2], element, absolute_us)
    b = point_after_pose(box[2:], element, absolute_us)
    return a+b


def glyph_raster(element):
    font_file = covered_font(element['text'],element.get('text_bold',True),element.get('font_file'))
    font_px = element.get('proxy_font_px', element.get('text_size', 6)*12)
    if not Path(font_file).is_file():
        raise ValueError('Explicit font file missing: '+font_file)
    font = ImageFont.truetype(font_file, max(1, round(font_px)))
    stroke = round(element.get('text_stroke', {}).get('width_px', 0))
    spacing = round(element.get('text_line_spacing_px', 4))
    draw = ImageDraw.Draw(Image.new('L', (1, 1)))
    b = draw.multiline_textbbox((0, 0), element['text'], font=font, spacing=spacing, stroke_width=stroke,
                               align='center')
    # Multiline textbbox can contain half-pixel coordinates for centred lines.
    # Enclose the measured bounds instead of truncating ink or changing font size.
    raster_bounds = (math.floor(b[0]), math.floor(b[1]), math.ceil(b[2]), math.ceil(b[3]))
    out = Image.new('RGBA', (max(1, raster_bounds[2]-raster_bounds[0]),
                             max(1, raster_bounds[3]-raster_bounds[1])))
    ImageDraw.Draw(out).multiline_text((-raster_bounds[0], -raster_bounds[1]), element['text'], font=font, spacing=spacing,
        align='center', fill=element.get('text_color', '#243C45'), stroke_width=stroke,
        stroke_fill=element.get('text_stroke', {}).get('color', '#FFFFFF'))
    return out, {'font_file': font_file, 'measurement_font_px': font_px,
        'unicode_cmap_coverage_verified': True,
        'pillow_ink_bbox_px': list(out.getchannel('A').getbbox()),
        'font_native_render_verified': False,
        'scope': 'actual measured Pillow glyph ink; target editor font raster requires native sample'}


def glyph_box(element):
    im, info = glyph_raster(element)
    b = element['bbox_px']; cx, cy = (b[0]+b[2])/2, (b[1]+b[3])/2
    ink = im.getchannel('A').getbbox()
    return [cx-im.width/2+ink[0], cy-im.height/2+ink[1],
            cx-im.width/2+ink[2], cy-im.height/2+ink[3]], info


def nearest_rectangle_edge(point, box):
    """Nearest boundary point; does not invent an anatomical target."""
    x, y = point; x0, y0, x1, y1 = box
    candidates = [[x0, max(y0, min(y1, y))], [x1, max(y0, min(y1, y))],
                  [max(x0, min(x1, x)), y0], [max(x0, min(x1, x)), y1]]
    return min(candidates, key=lambda q: (q[0]-x)**2+(q[1]-y)**2)


class Geometry:
    def __init__(self, elements, base, canvas=(1920, 1080)):
        self.elements = {e['id']: e for e in elements}
        self.base = Path(base); self.canvas = tuple(canvas)
        self._rasters = {}; self._geometry = {}; self._glyph_info = {}
        self._alphas = {}; self._ink_boxes = {}

    def path(self, value):
        p = Path(value)
        return p if p.is_absolute() else self.base/p

    def raster(self, eid):
        if eid not in self._rasters:
            e = self.elements[eid]
            if e['type'] == 'png':
                with Image.open(self.path(e['asset'])) as raw:
                    im, info = prepare_png_canvas(raw, e, self.canvas)
                self._geometry[eid] = info
            elif e['type'] == 'text':
                ink, info = glyph_raster(e)
                im = Image.new('RGBA', self.canvas)
                b = e['bbox_px']; cx, cy = (b[0]+b[2])/2, (b[1]+b[3])/2
                im.alpha_composite(ink, (round(cx-ink.width/2), round(cy-ink.height/2)))
                self._glyph_info[eid] = info
            else:
                raise ValueError('Actual alpha geometry only supports PNG/native text: '+eid)
            self._rasters[eid] = im
        return self._rasters[eid]

    def ink_box(self, eid, absolute_us=None):
        if eid not in self._ink_boxes:
            self._ink_boxes[eid] = list(self.alpha(eid).getbbox())
        box = list(self._ink_boxes[eid])
        return box_after_pose(box, self.elements[eid], absolute_us) if absolute_us is not None else box

    def alpha(self, eid):
        if eid not in self._alphas:
            self._alphas[eid] = self.raster(eid).getchannel('A')
        return self._alphas[eid]

    def source_point(self, eid, source_point_px, absolute_us=None, require_source_ink=True):
        e = self.elements[eid]
        if e['type'] != 'png':
            raise ValueError('Source-canvas anchor requires an actual PNG: '+eid)
        self.raster(eid); g = self._geometry[eid]
        x, y = source_point_px; sw, sh = g['source_canvas_px']
        if not (0 <= x < sw and 0 <= y < sh):
            raise ValueError('Anchor outside original source canvas: '+eid)
        if require_source_ink:
            with Image.open(self.path(e['asset'])) as raw:
                near = raw.convert('RGBA').getchannel('A').crop((max(0, int(x)-3), max(0, int(y)-3),
                    min(sw, int(x)+4), min(sh, int(y)+4)))
                if near.getextrema()[1] < 32:
                    raise ValueError('Scientific source anchor is on transparent pixels: '+eid)
        crop = [0, 0, sw, sh] if not g['independent_alpha_trim_applied'] else g['source_alpha_bbox_px']
        f = g['fitted_canvas_bbox_px']
        point = [f[0]+(x-crop[0])*(f[2]-f[0])/(crop[2]-crop[0]),
                 f[1]+(y-crop[1])*(f[3]-f[1])/(crop[3]-crop[1])]
        return point_after_pose(point, e, absolute_us) if absolute_us is not None else point

    def alpha_roi(self, eid, absolute_us, box):
        e = self.elements[eid]; p = pose(e, absolute_us)
        width, height = max(1, box[2]-box[0]), max(1, box[3]-box[1])
        if not e['start_us'] <= absolute_us < e['end_us'] or p['alpha'] <= 0:
            return Image.new('L', (width, height))
        b=e['bbox_px'];cx,cy=(b[0]+b[2])/2,(b[1]+b[3])/2;s=p['scale']
        dx=(1-s)*cx+p['dx_px'];dy=(1-s)*cy+p['dy_px']
        im=self.alpha(eid).transform((width,height),Image.Transform.AFFINE,
            (1/s,0,(box[0]-dx)/s,0,1/s,(box[1]-dy)/s),Image.Resampling.BILINEAR)
        if p['alpha'] < 1:
            im=im.point([round(v*p['alpha']) for v in range(256)])
        return im


def timeline_samples(a, b, elements, fps=30):
    samples = {a, b-1}
    first = math.ceil(a*fps/1e6)
    samples.update(round(n*1e6/fps) for n in range(first, math.ceil(b*fps/1e6)))
    for e in elements:
        samples.update(e['start_us']+p['at_us'] for p in e.get('animation', {}).get('keyframes', []))
    return sorted(t for t in samples if a <= t < b)


def intersection_box(a, b, canvas):
    x0=max(0, math.floor(max(a[0],b[0])));y0=max(0, math.floor(max(a[1],b[1])))
    x1=min(canvas[0], math.ceil(min(a[2],b[2])));y1=min(canvas[1],math.ceil(min(a[3],b[3])))
    return [x0,y0,x1,y1] if x0<x1 and y0<y1 else None


def collision_samples(elements, base, canvas=(1920,1080), fps=30, policies=()):
    """No composition-group waiver. Structural registration is separately logged.

    Every actual glyph/PNG intersection is measured. Explicit pair policies
    remain visible in the report and must carry a human reason. Endpoint-only
    policies cannot excuse the rest of a leader crossing a target or label.
    """
    import numpy as np
    g=Geometry(elements,base,canvas);out=[];exceptions={}
    for p in policies:
        pair=p.get('element_ids',[])
        if len(pair)!=2 or not p.get('reason'):
            raise ValueError('Overlap permission requires exactly two IDs and a reason')
        windows=p.get('allowed_windows_us')
        if windows is not None and (not windows or any(
                not isinstance(w,list) or len(w)!=2 or not all(isinstance(t,int) for t in w) or w[0]>=w[1]
                for w in windows)):
            raise ValueError('Transition overlap windows must be explicit positive absolute-time intervals')
        exceptions[frozenset(pair)]=p
    for i,a in enumerate(elements):
        if a['type'] not in ('png','text'):continue
        for b in elements[i+1:]:
            if b['type'] not in ('png','text'):continue
            begin=max(a['start_us'],b['start_us']);end=min(a['end_us'],b['end_us'])
            if begin>=end:continue
            pair=frozenset((a['id'],b['id']));policy=exceptions.get(pair)
            registration=(a['type']==b['type']=='png' and a.get('registration_group') and
                          a.get('registration_group')==b.get('registration_group'))
            max_pixels=0;first_at=None;outside_endpoint=False;outside_window=False
            for t in timeline_samples(begin,end,(a,b),fps):
                if pose(a,t)['alpha']<.05 or pose(b,t)['alpha']<.05:continue
                roi=intersection_box(g.ink_box(a['id'],t),g.ink_box(b['id'],t),canvas)
                if not roi:continue
                overlap=(np.asarray(g.alpha_roi(a['id'],t,roi))>=32)&(np.asarray(g.alpha_roi(b['id'],t,roi))>=32)
                pixels=int(overlap.sum())
                if pixels:
                    if first_at is None:first_at=t
                    max_pixels=max(max_pixels,pixels)
                    if policy and policy.get('allowed_windows_us') is not None:
                        outside_window |= not any(a<=t<=b for a,b in policy['allowed_windows_us'])
                    if policy and policy.get('endpoint_only'):
                        anchor=policy['anchor_px'];parent=policy.get('transform_parent_id')
                        if parent:anchor=point_after_pose(anchor,g.elements[parent],t)
                        yy,xx=np.nonzero(overlap)
                        outside_endpoint |= bool((((xx+roi[0]-anchor[0])**2+(yy+roi[1]-anchor[1])**2)>
                                                   policy.get('radius_px',8)**2).any())
            if not max_pixels:continue
            approved=bool(registration or (policy and not outside_endpoint and not outside_window))
            out.append({'element_ids':[a['id'],b['id']],'first_intersection_us':first_at,
                'max_actual_alpha_intersection_pixels':max_pixels,'approved':approved,
                'approval_kind':'shared_registered_canvas' if registration else policy.get('relation') if policy else None,
                'reason':'same-source registered anatomy/occlusion composition, not a text waiver' if registration else
                         policy.get('reason') if policy else None,'endpoint_policy_exceeded':outside_endpoint,
                'time_window_policy_exceeded':outside_window,
                'composition_group_waiver_applied':False})
    return {'ok':all(r['approved'] for r in out),'actual_intersections':out,
        'unapproved_intersections':[r for r in out if not r['approved']],
        'sample_fps':fps,'native_font_raster_verified':False,
        'scope':f'actual PNG alpha and measured glyph masks at every {fps}fps frame plus authored KF times; visual judgement remains required'}
