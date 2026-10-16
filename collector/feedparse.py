"""RSS 2.0 / Atom parser built on xml.etree (no third-party dependencies)."""

import html
import re
import xml.etree.ElementTree as ET

from .textutil import clip, parse_date, strip_html

ATOM_NS = "{http://www.w3.org/2005/Atom}"
DECL_RE = re.compile(r"encoding\s*=\s*[\"']([\w\-]+)[\"']", re.I)
NAMED_ENT_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]{1,31});")
XML_BUILTIN = {"amp", "lt", "gt", "quot", "apos"}

# Element names that carry the fields we care about, by local tag name.
TITLE_NAMES = {"title"}
SUMMARY_NAMES = {"description", "summary", "content", "encoded"}
DATE_NAMES = {"pubdate", "published", "updated", "date", "created"}
AUTHOR_NAMES = {"creator", "author"}
ID_NAMES = {"guid", "id"}


def _sanitize_xml(text):
    """Feed bodies regularly contain HTML entities that XML does not define."""

    def replace(match):
        name = match.group(1)
        if name in XML_BUILTIN:
            return match.group(0)
        decoded = html.unescape(match.group(0))
        if decoded == match.group(0):
            return "&amp;" + name + ";"
        return decoded.replace("&", "&amp;")

    return NAMED_ENT_RE.sub(replace, text)


def _to_text(payload):
    if isinstance(payload, str):
        return payload
    head = payload[:300].decode("ascii", errors="ignore")
    match = DECL_RE.search(head)
    encoding = match.group(1) if match else "utf-8"
    try:
        return payload.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return payload.decode("utf-8", errors="replace")


def parse_feed(payload, source, summary_limit=600):
    """Parse a feed payload into raw item dicts.

    Returns [] for anything that is not a recognisable feed, so a broken source
    degrades to zero items instead of aborting the whole run.
    """
    if not payload:
        return []
    try:
        root = ET.fromstring(_sanitize_xml(_to_text(payload)))
    except ET.ParseError:
        return []

    items = []
    for node in root.iter():
        tag = node.tag
        if not isinstance(tag, str):
            continue
        local = tag.split("}")[-1]
        if local not in ("item", "entry"):
            continue
        parsed = _parse_node(node, source, summary_limit)
        if parsed:
            items.append(parsed)
    return items


def _child_text(node, names):
    for child in node:
        if not isinstance(child.tag, str):
            continue
        if child.tag.split("}")[-1].lower() not in names:
            continue
        text = "".join(child.itertext()).strip()
        if text:
            return text
    return ""


def _find_link(node):
    """Resolve the item URL across RSS (`<link>text`) and Atom (`<link href>`)."""
    for child in node:
        if not isinstance(child.tag, str) or child.tag.split("}")[-1].lower() != "link":
            continue
        rel = (child.get("rel") or "alternate").lower()
        href = (child.get("href") or "").strip()
        if href and rel in ("alternate", ""):
            return href
    for child in node:
        if not isinstance(child.tag, str) or child.tag.split("}")[-1].lower() != "link":
            continue
        href = (child.get("href") or "").strip()
        if href:
            return href
        text = (child.text or "").strip()
        if text.startswith("http"):
            return text
    return ""


def _find_publisher(node):
    """Google News style `<source url="...">Publisher</source>`."""
    for child in node:
        if not isinstance(child.tag, str) or child.tag.split("}")[-1].lower() != "source":
            continue
        return {"name": "".join(child.itertext()).strip(), "url": (child.get("url") or "").strip()}
    return {"name": "", "url": ""}


def _parse_node(node, source, summary_limit):
    title = strip_html(_child_text(node, TITLE_NAMES))
    url = _find_link(node)
    if not title or not url.startswith("http"):
        return None

    summary_source = _child_text(node, {"description", "summary"}) or _child_text(node, {"content", "encoded"})
    published = parse_date(_child_text(node, DATE_NAMES))
    publisher = _find_publisher(node)

    return {
        "source_id": source["id"],
        "source_name": source.get("publisher") or publisher["name"] or source["name"],
        "lang": source["lang"],
        "title": clip(title, 300),
        "url": url,
        "summary": clip(strip_html(summary_source), summary_limit),
        "published_at": published,
        "author": strip_html(_child_text(node, AUTHOR_NAMES)),
        "guid": _child_text(node, ID_NAMES),
        "publisher_domain": publisher["url"],
    }
