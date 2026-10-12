"""Discover runtime resources; do not embed an author's Windows profile paths."""
import os
from pathlib import Path
import shutil
import subprocess


def find_font(explicit=None):
    """Font metrics are a conservative check, not proof of the editor's font rendering."""
    supplied = explicit or os.environ.get('EXPLAINER_VIDEO_FONT')
    if supplied:
        p = Path(supplied).expanduser().resolve()
        if not p.is_file():
            raise ValueError('Configured subtitle measurement font is missing: ' + str(p))
        return p
    candidates = []
    if os.environ.get('WINDIR'):
        candidates += [Path(os.environ['WINDIR'])/'Fonts'/n for n in ('msyhbd.ttc','msyh.ttc','arialbd.ttf', 'segoeuib.ttf', 'arial.ttf')]
    candidates += [Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),
                   Path('/System/Library/Fonts/PingFang.ttc'),
                   Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'),
                   Path('/System/Library/Fonts/Supplemental/Arial Bold.ttf')]
    for p in candidates:
        if p.is_file():
            return p.resolve()
    raise ValueError('No measurement font found; set EXPLAINER_VIDEO_FONT to a local TTF/OTF file')


def font_diagnostics(path):
    from PIL import ImageFont
    font=ImageFont.truetype(str(path),48)
    masks=[bytes(font.getmask(char)) for char in ('中','文','心')]
    distinct=len(set(masks))==3 and all(masks)
    return {'family':font.getname()[0],'style':font.getname()[1],
            'sample_han_glyphs_distinct':bool(distinct), 'tested_characters':'中文心',
            'full_unicode_coverage_verified':False,'native_font_verified':False,
            'measurement_limit':'Only three Han glyphs were probed; verify the actual language and native editor rendering.',
            'cjk_font_action':None if distinct else 'For CJK subtitles set EXPLAINER_VIDEO_FONT to an installed CJK TTF/OTF/TTC font.'}


def ffmpeg_diagnostics(required=False):
    configured=os.environ.get('EXPLAINER_VIDEO_FFMPEG') or os.environ.get('SCIENCE_VIDEO_FFMPEG')
    selected=configured or shutil.which('ffmpeg')
    origin='environment' if configured else 'PATH'
    if not selected:
        try:
            import imageio_ffmpeg
            selected=imageio_ffmpeg.get_ffmpeg_exe();origin='imageio_ffmpeg'
        except (ImportError,RuntimeError):
            pass
    usable=False;version=None;error=None
    if selected:
        try:
            run=subprocess.run([str(selected),'-version'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=8)
            usable=run.returncode==0
            version=run.stdout.splitlines()[0] if run.stdout.splitlines() else None
            if not usable:error='ffmpeg -version failed with code '+str(run.returncode)
        except (OSError,subprocess.TimeoutExpired) as exc:error=str(exc)
    else:error='FFmpeg not found; image/WAV-only native draft authoring does not require it.'
    return {'ok':usable or not required,'required':bool(required),'available':usable,'path':str(selected) if selected else None,
            'discovery':origin if selected else None,'version':version,'error':error,
            'needed_for':'video-first-shot cover extraction or explicitly requested preview/export workflows',
            'native_editor_export_verified':False}
