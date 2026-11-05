"""Session-bound raster attachments discovered in native messages, never paths from HTTP."""
import base64
import os
from pathlib import Path
import re
import secrets
import stat
import struct
import time
import threading

from .broker import ControlError

MAX_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 16000000
ID = re.compile(r'[A-Za-z0-9_-]{32}')
ERRORS = {'attachment_unavailable', 'attachment_changed', 'attachment_too_large', 'attachment_unsupported'}
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}


def image_info(data):
    """Inspect raster headers without executing a decoder or accepting SVG/HTML."""
    kind, width, height = None, 0, 0
    if data.startswith(b'\x89PNG\r\n\x1a\n') and data[12:16] == b'IHDR' and len(data) >= 24:
        kind, (width, height) = 'image/png', struct.unpack('>II', data[16:24])
    elif data[:6] in (b'GIF87a', b'GIF89a') and len(data) >= 10:
        kind, (width, height) = 'image/gif', struct.unpack('<HH', data[6:10])
    elif data[:2] == b'\xff\xd8':
        offset = 2
        while offset + 4 <= len(data):
            if data[offset] != 255:
                break
            marker = data[offset + 1]
            if marker == 255:
                offset += 1
                continue
            size = int.from_bytes(data[offset + 2:offset + 4], 'big')
            if size < 2:
                break
            if marker in (0xc0, 0xc1, 0xc2) and offset + 9 <= len(data):
                height, width = struct.unpack('>HH', data[offset + 5:offset + 9])
                kind = 'image/jpeg'
                break
            offset += 2 + size
    elif data[:4] == b'RIFF' and data[8:12] == b'WEBP' and len(data) >= 30:
        if data[12:16] == b'VP8X':
            width, height = 1 + int.from_bytes(data[24:27], 'little'), 1 + int.from_bytes(data[27:30], 'little')
        elif data[12:16] == b'VP8 ' and data[23:26] == b'\x9d\x01\x2a':
            width, height = (n & 0x3fff for n in struct.unpack('<HH', data[26:30]))
        elif data[12:16] == b'VP8L' and data[20] == 0x2f:
            value = int.from_bytes(data[21:25], 'little')
            width, height = 1 + (value & 0x3fff), 1 + ((value >> 14) & 0x3fff)
        kind = 'image/webp'
    if not kind or not width or not height:
        raise ControlError('attachment_unsupported')
    if width * height > MAX_PIXELS or max(width, height) > 16000:
        raise ControlError('attachment_too_large')
    return kind, width, height


def image_references(text):
    """Only image syntax counts as a reference; ordinary file links grant no read."""
    if not isinstance(text, str):
        return []
    text = re.sub(r'```[^\n]*\n[\s\S]*?(?:```|$)|`[^`\n]*`', '', text[:200000])
    found = []
    for match in re.finditer(r'!\[[^\]\n]{0,500}\]\(', text):
        tail = text[match.end():]
        if tail.startswith('<'):
            end = tail.find('>')
            if end < 0 or not re.match(r'\s*(?:[\"\'][^\n]*[\"\']\s*)?\)', tail[end + 1:]):
                continue
            source = tail[1:end]
        else:
            depth, end, escaped = 0, None, False
            for index, char in enumerate(tail[:2100]):
                if escaped:
                    escaped = False
                    continue
                if char == '\\':
                    escaped = True
                elif char == '(':
                    depth += 1
                elif char == ')':
                    if not depth:
                        end = index
                        break
                    depth -= 1
            if end is None:
                continue
            source = re.sub(r'\s+[\"\'][^\n]*[\"\']\s*$', '', tail[:end]).strip()
            source = re.sub(r'\\([()])', r'\1', source)
        found.append(source)
    for match in re.finditer(r'<image\b[^>]{0,2500}\bpath=([\"\'])(.*?)\1[^>]*>', text):
        found.append(match.group(2))
    return list(dict.fromkeys(source for source in found if source and len(source) <= 2048))[:8]


def _fingerprint(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


class Attachments:
    def __init__(self, clock=time.monotonic):
        self.clock, self.entries = clock, {}
        self.lock = threading.RLock()

    def prune(self):
        with self.lock:
            now = self.clock()
            self.entries = {key: value for key, value in self.entries.items() if now - value['at'] < 600}

    def describe(self, sid, text, native_path):
        with self.lock:
            return self._describe(sid, text, native_path)

    def _describe(self, sid, text, native_path):
        self.prune()
        result = []
        for source in image_references(text):
            path = Path(source)
            if (not path.is_absolute() or source.startswith(('//', '\\\\')) or '\x00' in source
                    or '\n' in source or path.suffix.lower() not in EXTENSIONS):
                continue
            descriptor = {'source': source, 'name': path.name[:200]}
            try:
                target = path.resolve(strict=True)
                info = target.stat()
                if not stat.S_ISREG(info.st_mode):
                    raise ControlError('attachment_unavailable')
                if not 0 < info.st_size <= MAX_BYTES:
                    raise ControlError('attachment_too_large')
                fd = os.open(target, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
                with os.fdopen(fd, 'rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode) or _fingerprint(opened) != _fingerprint(info):
                        raise ControlError('attachment_changed')
                    mime, width, height = image_info(stream.read(65536))
                    if _fingerprint(os.fstat(stream.fileno())) != _fingerprint(info):
                        raise ControlError('attachment_changed')
                fingerprint = _fingerprint(info)
                native = native_path.stat()
                binding = (sid, source, fingerprint, native.st_dev, native.st_ino)
                existing = next(((key, value) for key, value in self.entries.items() if value['binding'] == binding), None)
                if existing:
                    identity, entry = existing
                    entry['at'] = self.clock()
                else:
                    if len(self.entries) >= 256:
                        self.entries.pop(next(iter(self.entries)))
                    identity = secrets.token_urlsafe(24)
                    self.entries[identity] = {'binding': binding, 'target': target, 'at': self.clock(), 'mime': mime}
                descriptor.update(id=identity, mime=mime, width=width, height=height)
            except ControlError as error:
                descriptor['error'] = error.code
            except (OSError, ValueError):
                descriptor['error'] = 'attachment_unavailable'
            result.append(descriptor)
        return result

    def read(self, sid, identity, native_path):
        with self.lock:
            return self._read(sid, identity, native_path)

    def _read(self, sid, identity, native_path):
        self.prune()
        entry = self.entries.get(identity) if isinstance(identity, str) and ID.fullmatch(identity) else None
        if not entry or entry['binding'][0] != sid:
            raise ControlError('attachment_unavailable')
        _, source, fingerprint, device, inode = entry['binding']
        try:
            native = native_path.stat()
            if (native.st_dev, native.st_ino) != (device, inode):
                raise ControlError('attachment_changed')
            if Path(source).resolve(strict=True) != entry['target']:
                raise ControlError('attachment_changed')
            fd = os.open(entry['target'], os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(fd, 'rb') as stream:
                opened = os.fstat(stream.fileno())
                if not stat.S_ISREG(opened.st_mode) or _fingerprint(opened) != fingerprint:
                    raise ControlError('attachment_changed')
                data = stream.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES or _fingerprint(os.fstat(stream.fileno())) != fingerprint:
                    raise ControlError('attachment_changed')
            mime, _, _ = image_info(data)
            if mime != entry['mime']:
                raise ControlError('attachment_changed')
            return {'media': {'mime': mime, 'data': base64.b64encode(data).decode('ascii')}}
        except OSError:
            raise ControlError('attachment_unavailable') from None


def validate_media(value):
    if not isinstance(value, dict) or set(value) != {'mime', 'data'} or not isinstance(value['data'], str):
        raise ControlError('invalid_attachment_result', 400)
    if len(value['data']) > (MAX_BYTES + 2) // 3 * 4:
        raise ControlError('invalid_attachment_result', 400)
    try:
        data = base64.b64decode(value['data'], validate=True)
        mime, _, _ = image_info(data)
    except (ValueError, ControlError):
        raise ControlError('invalid_attachment_result', 400) from None
    if len(data) > MAX_BYTES or mime != value['mime']:
        raise ControlError('invalid_attachment_result', 400)
    return data


def clean_descriptors(value):
    if not isinstance(value, list) or len(value) > 8:
        raise ControlError('invalid_attachment_result', 400)
    out = []
    for item in value:
        if (not isinstance(item, dict) or set(item) - {'id', 'source', 'name', 'mime', 'width', 'height', 'error'}
                or any(not isinstance(item.get(key), str) or not item[key] or '\x00' in item[key]
                       or len(item[key]) > size for key, size in (('source', 2048), ('name', 200)))):
            raise ControlError('invalid_attachment_result', 400)
        if 'id' in item:
            if (not isinstance(item['id'], str) or not ID.fullmatch(item['id'])
                    or item.get('mime') not in ('image/png', 'image/jpeg', 'image/gif', 'image/webp')
                    or any(type(item.get(key)) is not int or not 1 <= item[key] <= 16000 for key in ('width', 'height'))
                    or item['width'] * item['height'] > MAX_PIXELS or 'error' in item):
                raise ControlError('invalid_attachment_result', 400)
        elif item.get('error') not in ERRORS:
            raise ControlError('invalid_attachment_result', 400)
        out.append(dict(item))
    return out
