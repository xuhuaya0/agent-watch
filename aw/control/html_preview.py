"""Static, network-free HTML previews; never serve an artifact as application UI."""
import base64
import html
from html.parser import HTMLParser
from .broker import ControlError

CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; sandbox"
TAGS = set('html head body title style div span p br hr h1 h2 h3 h4 h5 h6 main section article header footer nav aside figure figcaption table caption colgroup col thead tbody tfoot tr th td ul ol li dl dt dd pre code blockquote strong b em i u s small sub sup mark abbr time details summary a img'.split())
VOID = {'br', 'hr', 'col', 'img'}
ATTRS = {'class', 'id', 'style', 'title', 'lang', 'dir', 'width', 'height', 'colspan', 'rowspan', 'scope', 'alt', 'open', 'start', 'reversed'}


def preview_html(data):
    # UTF-8 is the supported artifact encoding; callers enforce the byte limit.
    text = data.decode('utf-8-sig')
    class Preview(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.out, self.blocked, self.in_style = [], [], False
        def handle_starttag(self, tag, attrs):
            if self.blocked:
                if tag not in VOID: self.blocked.append(tag)
                return
            if tag in ('script', 'iframe', 'object', 'embed', 'svg', 'math', 'template'):
                if tag not in ('embed',): self.blocked.append(tag)
                return
            if tag not in TAGS: return
            kept = []
            for name, value in attrs:
                if name in ATTRS:
                    kept.append((name, value or ''))
                elif tag == 'img' and name == 'src' and value:
                    # Only self-contained, validated raster data; no local/network fetches.
                    try:
                        from .attachments import image_info, MAX_BYTES
                        prefix, encoded = value.split(',', 1)
                        raw = base64.b64decode(encoded, validate=True)
                        mime, _, _ = image_info(raw)
                        if len(raw) <= MAX_BYTES and prefix == 'data:'+mime+';base64':
                            kept.append(('src', value))
                    except (ValueError, ControlError):
                        # Invalid inline images are omitted without exposing parser errors.
                        pass
            self.out.append('<'+tag+''.join(' '+n+'="'+html.escape(v, quote=True)+'"' for n,v in kept)+'>')
            if tag == 'style': self.in_style = True
        def handle_startendtag(self, tag, attrs):
            self.handle_starttag(tag, attrs)
            if tag not in VOID: self.handle_endtag(tag)
        def handle_endtag(self, tag):
            if self.blocked:
                if tag in self.blocked:
                    self.blocked = self.blocked[:len(self.blocked)-1-self.blocked[::-1].index(tag)]
                return
            if tag in TAGS and tag not in VOID: self.out.append('</'+tag+'>')
            if tag == 'style': self.in_style = False
        def handle_data(self, value):
            if not self.blocked:self.out.append(value if self.in_style else html.escape(value))
    parser = Preview(); parser.feed(text); parser.close()
    result = ('<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="'+
              html.escape(CSP,quote=True)+'">'+''.join(parser.out)).encode('utf-8')
    if len(result) > 4 * 1024 * 1024: raise ControlError('attachment_too_large')
    return result
