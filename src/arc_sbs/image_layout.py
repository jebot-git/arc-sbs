"""Explicit stereo image recognition; wide panoramas alone are not SBS evidence."""
from pathlib import Path
import re
import zlib

LAYOUTS = ('auto', '2d', 'full-sbs', 'half-sbs')


def file_metadata(name):
    """Read bounded JPEG APP3 JPS / PNG sTER descriptors without decoding pixels."""
    try:
        with Path(name).open('rb') as stream:
            signature = stream.read(8)
            if signature.startswith(b'\xff\xd8'):
                stream.seek(2)
                while stream.tell() < 1024 * 1024:
                    if stream.read(1) != b'\xff': break
                    marker = stream.read(1)
                    while marker == b'\xff': marker = stream.read(1)
                    if not marker or marker in (b'\xda', b'\xd9'): break
                    length = int.from_bytes(stream.read(2), 'big')
                    if length < 2: break
                    payload = stream.read(length - 2)
                    if marker == b'\xe3' and payload.startswith(b'_JPSJPS_') and len(payload) >= 14:
                        flags, layout, kind = payload[11:14]
                        if kind == 0: return {'stereo-layout': '2d'}
                        if kind != 1 or layout != 2 or flags & 1:
                            return {'stereo-layout': 'unsupported-jps'}
                        return {'stereo-layout': 'half-sbs' if flags & 2 else 'full-sbs',
                                'stereo-order': 'left_right' if flags & 4 else 'right_left'}
            elif signature == b'\x89PNG\r\n\x1a\n':
                width = None
                while stream.tell() < 1024 * 1024:
                    header = stream.read(8)
                    if len(header) != 8: break
                    length, kind = int.from_bytes(header[:4], 'big'), header[4:]
                    if kind in (b'IDAT', b'IEND') or length > 1024 * 1024: break
                    data, crc = stream.read(length), stream.read(4)
                    if len(data) != length or len(crc) != 4: break
                    if kind == b'IHDR' and length == 13: width = int.from_bytes(data[:4], 'big')
                    if kind == b'sTER' and length == 1 and data[0] in (0, 1):
                        if zlib.crc32(kind + data) & 0xffffffff != int.from_bytes(crc, 'big'): break
                        for padding in range(8):
                            if width and (width - padding) % 2 == 0:
                                eye = (width - padding) // 2
                                if eye > 0 and (-eye) % 8 == padding:
                                    return {'stereo-layout': 'full-sbs',
                                            'stereo-order': 'left_right' if data[0] else 'right_left',
                                            'stereo-eye-width': eye, 'stereo-image-width': width}
                        return {'stereo-layout': 'invalid-png-stereo'}
    except (OSError, ValueError):
        pass
    return {}


def detect_layout(name='', metadata=None, requested='auto'):
    if requested not in LAYOUTS:
        raise ValueError(f'Unknown image input layout: {requested}')
    if requested != 'auto':
        return requested, False, 'manual override'
    metadata = {str(k).lower().replace('_', '-'): str(v).lower().strip()
                for k, v in (metadata or {}).items()}
    values = {'left-right': ('full-sbs', False), 'right-left': ('full-sbs', True),
              'side-by-side': ('full-sbs', False), 'sbs': ('full-sbs', False),
              'full-sbs': ('full-sbs', False), 'half-sbs': ('half-sbs', False),
              'hsbs': ('half-sbs', False), 'mono': ('2d', False), '2d': ('2d', False)}
    for key in ('stereo-layout', 'stereo-mode', 'stereomode', 'stereo'):
        value = metadata.get(key, '').replace('_', '-')
        if value in ('unsupported-jps', 'invalid-png-stereo'):
            raise ValueError('Image stereo descriptor is not supported SBS; choose an explicit input layout to override')
        if value in values:
            layout, swap = values[value]
            swap = swap or metadata.get('stereo-order', '').replace('_', '-') == 'right-left'
            return layout, swap, f'image metadata: {key}'
    path = Path(name)
    stem = path.stem.upper()
    def token(pattern):
        return bool(re.search(r'(?<![A-Z0-9])(?:' + pattern + r')(?![A-Z0-9])', stem))
    if token(r'H[-_. ]?SBS|HALF[-_. ]?SBS'):
        layout = 'half-sbs'
    elif token(r'F[-_. ]?SBS|FULL[-_. ]?SBS|SBS'):
        layout = 'full-sbs'
    elif path.suffix.lower() in ('.jps', '.pns'):
        return 'full-sbs', not token(r'LR|PARALLEL'), 'stereo extension (cross-eyed pair by convention)'
    else:
        return '2d', False, 'ordinary image; no explicit stereo marker'
    return layout, token(r'RL|CROSS|CROSS[-_. ]?EYED'), 'stereo filename or extension'
