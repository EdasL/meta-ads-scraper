"""Pure parser for a web-sub funnel's display identity: OG title/description and
favicon, extracted from the landing page HTML. No network — the caller fetches."""
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit


@dataclass(frozen=True)
class WebIdentity:
    name: str | None
    description: str | None
    icon_url: str | None


class _Head(HTMLParser):
    def __init__(self):
        super().__init__()
        self.og_title = None
        self.og_desc = None
        self.og_image = None
        self.meta_desc = None
        self.icon_href = None
        self.title = None
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            prop = a.get("property", "").lower()
            name = a.get("name", "").lower()
            content = a.get("content")
            if prop == "og:title" and content:
                self.og_title = content.strip()
            elif prop == "og:description" and content:
                self.og_desc = content.strip()
            elif prop == "og:image" and content:
                self.og_image = content.strip()
            elif name == "description" and content:
                self.meta_desc = content.strip()
        elif tag == "link":
            rels = a.get("rel", "").lower().split()
            if ("icon" in rels or "shortcut" in rels) and a.get("href") and not self.icon_href:
                self.icon_href = a["href"].strip()
        elif tag == "title":
            self._in_title = True

    def handle_data(self, data):
        if self._in_title and data.strip() and not self.title:
            self.title = data.strip()

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False


def parse_web_identity(html: str, base_url: str, *,
                       fallback_name: str | None = None) -> WebIdentity:
    p = _Head()
    try:
        p.feed(html or "")
    except Exception:
        pass
    name = p.og_title or p.title or fallback_name
    description = p.og_desc or p.meta_desc
    if p.icon_href:
        icon_url = urljoin(base_url, p.icon_href)
    elif p.og_image:
        icon_url = urljoin(base_url, p.og_image)
    else:
        parts = urlsplit(base_url)
        icon_url = f"{parts.scheme}://{parts.netloc}/favicon.ico" if parts.netloc else None
    return WebIdentity(name=name, description=description, icon_url=icon_url)
