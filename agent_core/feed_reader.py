"""Read titles, dates and article links from a bounded RSS or Atom download."""

from hashlib import sha256
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET


FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/rdf+xml", "application/xml", "text/xml")
ATOM = "{http://www.w3.org/2005/Atom}"
RSS1 = "{http://purl.org/rss/1.0/}"
RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
XML_BASE = "{http://www.w3.org/XML/1998/namespace}base"
MAX_ENTRIES = 30


class FeedTreeBuilder(ET.TreeBuilder):
    def doctype(self, name, pubid, system):
        raise ValueError("Feed document types are unsupported.")


def _article_url(base, href):
    if not href or not href.strip():
        return ""
    try:
        url = urljoin(base, href.strip())
        if any(character.isspace() for character in url):
            return ""
        address = urlsplit(url)
        if address.scheme in ("https", "http") and address.hostname and not address.username and not address.password:
            return url
    except ValueError:
        pass
    return ""


def extract_feed(body: bytes, url: str, max_chars: int, *, charset: str | None = None) -> dict:
    try:
        parser = ET.XMLParser(target=FeedTreeBuilder(), encoding=charset)
        root = ET.fromstring(body, parser=parser)
    except (ET.ParseError, LookupError) as error:
        raise ValueError("Invalid feed XML.") from error

    base = urljoin(url, root.get(XML_BASE, ""))
    if root.tag == "rss" and root.find("channel") is not None:
        channel = root.find("channel")
        base = urljoin(base, channel.get(XML_BASE, ""))
        entries = channel.findall("item")
        prefix = ""
    elif root.tag == RDF + "RDF" and root.find(RSS1 + "channel") is not None:
        entries = root.findall(RSS1 + "item")
        prefix = RSS1
    elif root.tag == ATOM + "feed":
        entries = root.findall(ATOM + "entry")
        prefix = ATOM
    else:
        raise ValueError("Expected an RSS channel or Atom feed.")

    lines = []
    entry_keys = []
    length = 0
    truncated = len(entries) > MAX_ENTRIES
    for entry in entries[:MAX_ENTRIES]:
        title_node = entry.find(prefix + "title")
        title = "" if title_node is None else " ".join("".join(title_node.itertext()).split())
        if not title and prefix != ATOM:
            description = entry.find(prefix + "description")
            if description is not None:
                title = " ".join("".join(description.itertext()).split())
        entry_base = urljoin(base, entry.get(XML_BASE, ""))
        link = ""
        for node in entry.findall(prefix + "link"):
            if node.get("rel", "alternate") == "alternate":
                link_base = urljoin(entry_base, node.get(XML_BASE, ""))
                link = _article_url(link_base, node.get("href") if prefix == ATOM else node.text)
                if link:
                    break
        if prefix == ATOM:
            date = entry.findtext(ATOM + "published") or entry.findtext(ATOM + "updated")
        else:
            date = entry.findtext("pubDate") or entry.findtext("{http://purl.org/dc/elements/1.1/}date")
        date = " ".join((date or "").split())
        publisher_id = entry.findtext(ATOM + "id") if prefix == ATOM else entry.findtext("guid")
        if prefix == RSS1:
            publisher_id = entry.get(RDF + "about")
        if publisher_id and publisher_id.strip():
            identity = "id:" + publisher_id.strip()
        elif link:
            identity = "url:" + link
        else:
            identity = "text:" + title + "\0" + date
        if len(title) > 500:
            title = title[:499] + "…"
            truncated = True
        fields = [title or "(untitled)", date, link]
        line = " | ".join(field for field in fields if field)
        extra = len(line) + int(bool(lines))
        if length + extra > max_chars:
            truncated = True
            break
        lines.append(line)
        entry_keys.append(sha256(identity.encode()).hexdigest())
        length += extra
    return {"content": "\n".join(lines), "truncated": truncated, "entry_keys": entry_keys}
