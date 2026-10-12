"""Explicit PNG layout, keeping registered scientific layers on one shared canvas.

The layout box is not the ink box. Source bytes and silhouette signatures remain
unchanged; these functions only derive a full-frame staging PNG.
"""
from pathlib import Path

REGISTERED_FIT = 'shared_registered_canvas_no_independent_alpha_fit'


def registered_canvas(element):
    flag = element.get('preserve_registered_canvas', False)
    if not isinstance(flag, bool):
        raise ValueError('preserve_registered_canvas must be boolean')
    mode = element.get('content_fit')
    if mode not in (None, 'alpha_bbox_contain', 'contain_alpha_content_without_stretch', REGISTERED_FIT):
        raise ValueError(f'Unsupported PNG content_fit: {mode}')
    if flag and mode in ('alpha_bbox_contain', 'contain_alpha_content_without_stretch'):
        raise ValueError('Conflicting PNG registered-canvas and alpha-trim modes')
    registered = flag or mode == REGISTERED_FIT
    group = element.get('registration_group')
    if registered and (not isinstance(group, str) or not group.strip()):
        raise ValueError('Registered PNG requires a nonempty registration_group')
    if group and not registered:
        raise ValueError('registration_group requires registered-canvas content_fit')
    return registered


def prepare_png_canvas(source, element, canvas_size):
    """Return staged RGBA and actual raster geometry; never mutate the source."""
    from PIL import Image
    raw = source.convert('RGBA')
    source_ink = raw.getchannel('A').getbbox()
    if source_ink is None:
        raise ValueError(f'Empty transparent PNG: {element.get("id")}')
    registered = registered_canvas(element)
    x0, y0, x1, y1 = [round(v) for v in element['bbox_px']]
    width, height = x1-x0, y1-y0
    if width <= 0 or height <= 0:
        raise ValueError('PNG layout rectangle must have positive dimensions')
    if registered:
        # Full RGBA dimensions carry tissue registration, including empty pixels.
        art = raw.copy()
        ratio = min(width/raw.width, height/raw.height)
        size = (max(1, round(raw.width*ratio)), max(1, round(raw.height*ratio)))
        if size != raw.size:
            art = art.resize(size, Image.Resampling.LANCZOS)
    else:
        art = raw.crop(source_ink)
        # V6's explicit art direction permits upscale to fill the target box,
        # keeping proportions. The old thumbnail path left real cutouts too small.
        ratio = min(width/art.width, height/art.height)
        size = (max(1, round(art.width*ratio)), max(1, round(art.height*ratio)))
        if size != art.size:
            art = art.resize(size, Image.Resampling.LANCZOS)
    left, top = x0+(width-art.width)//2, y0+(height-art.height)//2
    canvas = Image.new('RGBA', tuple(canvas_size))
    canvas.alpha_composite(art, (left, top))
    visible = canvas.getchannel('A').getbbox()
    if visible is None:
        raise ValueError('PNG placement has no visible ink on output canvas')
    geometry = {
        'fit_mode': REGISTERED_FIT if registered else 'alpha_bbox_contain',
        'source_canvas_px': list(raw.size),
        'source_alpha_bbox_px': list(source_ink),
        'layout_bbox_px': list(element['bbox_px']),
        'fitted_canvas_bbox_px': [left, top, left+art.width, top+art.height],
        'visible_bbox_px': list(visible),
        'registration_group': element.get('registration_group') if registered else None,
        'independent_alpha_trim_applied': not registered,
    }
    return canvas, geometry


def png_layout_geometry(asset_path, element, canvas_size):
    from PIL import Image
    with Image.open(asset_path) as source:
        _, geometry = prepare_png_canvas(source, element, canvas_size)
    return geometry


def validate_registration_groups(elements, base, canvas_size):
    """All layers in a registration group share actual dimensions and placement."""
    groups = {}
    for element in elements:
        if element.get('type') != 'png':
            if element.get('registration_group') or element.get('preserve_registered_canvas') or element.get('content_fit') == REGISTERED_FIT:
                raise ValueError('Registered-canvas layout only supports RGBA PNG layers')
            continue
        if not registered_canvas(element):
            continue
        asset = Path(element['asset'])
        asset = asset if asset.is_absolute() else Path(base)/asset
        geometry = png_layout_geometry(asset, element, canvas_size)
        group = element['registration_group']
        identity = (geometry['source_canvas_px'], geometry['layout_bbox_px'], geometry['fitted_canvas_bbox_px'])
        if group in groups and groups[group]['identity'] != identity:
            raise ValueError(f'Registration group canvas dimensions/layout differ: {group}')
        if group not in groups:
            groups[group] = {'identity': identity, 'elements': []}
        groups[group]['elements'].append(element['id'])
    return {key: {'source_canvas_px': value['identity'][0], 'layout_bbox_px': value['identity'][1],
                  'fitted_canvas_bbox_px': value['identity'][2], 'element_ids': value['elements']}
            for key, value in groups.items()}
