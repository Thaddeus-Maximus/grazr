"""Minimal Markdown → HTML for notes and entries: wikilinks, tags, images, lists."""
from __future__ import annotations

import re
from html import escape
from urllib.parse import quote

from markupsafe import Markup

from .ids import link_target
from .parse import HEADING, IMAGE_EXT, LIST

_INLINE = re.compile(
    r"(?P<embed>!\[\[(?P<et>[^\]\n]+)\]\])"
    r"|(?P<mdimg>!\[[^\]\n]*\]\((?P<mi>[^)\n]+)\))"
    r"|(?P<link>\[\[(?P<lt>[^\]\n]+)\]\])"
    r"|(?P<mdlink>\[(?P<mlt>[^\]\n]+)\]\((?P<mlu>[^)\s]+)\))"
    r"|(?P<code>`[^`\n]+`)"
    r"|(?P<bold>\*\*[^*\n]+\*\*)"
    r"|(?P<url>\bhttps?://[^\s<>\]]+)"
    r"|(?P<tag>(?:(?<=\s)|^)#[^\s#.,;:!?()\[\]{}\"'`]*[^\W\d_][^\s#.,;:!?()\[\]{}\"'`]*)"
)


class Renderer:
    """`href(target) → (url, css_class, (tag number, id) | None)` resolves a wikilink target."""

    def __init__(self, href, image_url):
        self.href = href
        self.image_url = image_url

    def inline(self, text: str) -> Markup:
        out = []
        pos = 0
        for m in _INLINE.finditer(text):
            out.append(escape(text[pos:m.start()]))
            pos = m.end()
            if m.group("embed"):
                t = m.group("et").split("|", 1)[0].strip()
                if t.lower().endswith(IMAGE_EXT):
                    out.append(f'<a href="{escape(self.image_url(t))}"><img class="embed" '
                               f'src="{escape(self.image_url(t))}" alt="{escape(t)}" loading="lazy"></a>')
                else:
                    out.append(self._link(t, t))
            elif m.group("mdimg"):
                src = m.group("mi").strip("<> ")
                url = src if src.startswith("http") else self.image_url(src)
                out.append(f'<img class="embed" src="{escape(url)}" alt="" loading="lazy">')
            elif m.group("link"):
                inner = m.group("lt")
                label = inner.split("|", 1)[1] if "|" in inner else link_target(inner)
                out.append(self._link(link_target(inner), label))
            elif m.group("mdlink"):
                out.append(f'<a href="{escape(m.group("mlu"))}">{escape(m.group("mlt"))}</a>')
            elif m.group("code"):
                out.append(f"<code>{escape(m.group('code')[1:-1])}</code>")
            elif m.group("bold"):
                out.append(f"<strong>{escape(m.group('bold')[2:-2])}</strong>")
            elif m.group("url"):
                u = m.group("url")
                out.append(f'<a href="{escape(u)}">{escape(u)}</a>')
            elif m.group("tag"):
                t = m.group("tag")[1:]
                out.append(f'<a class="tag" href="/t/{quote(t)}">#{escape(t)}</a>')
        out.append(escape(text[pos:]))
        return Markup("".join(out))

    def _link(self, target: str, label: str) -> str:
        url, cls, tag = self.href(target)
        if url is None:
            return f'<span class="link {cls}" title="unresolved">{escape(label)}</span>'
        title = ""
        if tag:   # (text, id): an animal's ear tag shows just its number (or name), even if aliased
            title = f' title="{escape(tag[1])}"'
            label = tag[0]
        return f'<a class="link {cls}" href="{escape(url)}"{title}>{escape(label)}</a>'

    def document(self, text: str) -> Markup:
        out = []
        in_code = False
        for i, line in enumerate(text.split("\n"), 1):
            line = line.rstrip("\r")
            if line.strip().startswith(("```", "~~~")):
                out.append("</pre>" if in_code else "<pre>")
                in_code = not in_code
                continue
            if in_code:
                out.append(escape(line) + "\n")
                continue
            if not line.strip():
                continue
            h = HEADING.match(line)
            if h:
                n = len(h.group(1))
                out.append(f'<h{n + 1} id="L{i}">{self.inline(h.group(2))}</h{n + 1}>')
                continue
            lm = LIST.match(line)
            if lm:
                depth = len(lm.group(1).expandtabs(4)) // 2
                out.append(f'<div class="li" id="L{i}" style="--depth:{depth}">{self.inline(lm.group(2))}</div>')
            else:
                out.append(f'<p id="L{i}">{self.inline(line.strip())}</p>')
        if in_code:
            out.append("</pre>")
        return Markup("\n".join(out))
