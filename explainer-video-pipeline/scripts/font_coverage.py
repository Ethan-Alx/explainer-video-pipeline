"""Deterministic local SFNT cmap coverage; never render a missing-glyph square.

This selects a real proxy font. Native editable text stays text, and target
editor font fallback remains an explicit native-preview check.
"""
from functools import lru_cache
from pathlib import Path
import struct
import os

@lru_cache(maxsize=24)
def glyph_codes(filename):
    data=Path(filename).read_bytes()
    u16=lambda p:struct.unpack_from('>H',data,p)[0]
    u32=lambda p:struct.unpack_from('>I',data,p)[0]
    # Pillow's default index is the first face, including TTC collections.
    sfnt=u32(12) if data[:4] == b'ttcf' else 0
    tables={data[sfnt+12+i*16:sfnt+16+i*16].decode('latin1'):u32(sfnt+20+i*16) for i in range(u16(sfnt+4))}
    base=tables['cmap']; codes=set()
    for i in range(u16(base+2)):
        p=base+4+i*8;platform,encoding=u16(p),u16(p+2)
        if platform not in (0,3):continue
        sub=base+u32(p+4);fmt=u16(sub)
        if fmt==12:
            for j in range(u32(sub+12)):
                q=sub+16+j*12;first,last,glyph=u32(q),u32(q+4),u32(q+8)
                codes.update(range(first+(glyph==0),last+1))
        elif fmt==4:
            n=u16(sub+6)//2;ends=sub+14;starts=ends+2*n+2;deltas=starts+2*n;offsets=deltas+2*n
            for j in range(n):
                first,last=u16(starts+2*j),u16(ends+2*j);delta=u16(deltas+2*j);offset=u16(offsets+2*j)
                for c in range(first,min(last,65534)+1):
                    glyph=(c+delta)&65535 if not offset else u16(offsets+2*j+offset+2*(c-first))
                    if offset and glyph:glyph=(glyph+delta)&65535
                    if glyph:codes.add(c)
    return frozenset(codes)

def covered_font(text,bold=True,explicit=None):
    need={ord(c) for c in text if not c.isspace()}
    preferred=explicit or os.environ.get('EXPLAINER_VIDEO_FONT')
    if preferred and not Path(preferred).expanduser().is_file():
        raise ValueError('Configured annotation measurement font is missing: '+str(preferred))
    candidates=[str(Path(preferred).expanduser())] if preferred else []
    if os.environ.get('WINDIR'):
        names=('arialbd.ttf','segoeuib.ttf','calibrib.ttf','msyhbd.ttc') if bold else ('arial.ttf','segoeui.ttf','calibri.ttf','msyh.ttc')
        candidates += [str(Path(os.environ['WINDIR'])/'Fonts'/name) for name in names]
    candidates += ['/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                   '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
                   '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
                   '/System/Library/Fonts/PingFang.ttc']
    for path in dict.fromkeys(candidates):
        if Path(path).is_file() and need<=glyph_codes(path):return path
    raise ValueError('No verified local font covers text '+repr(text))
