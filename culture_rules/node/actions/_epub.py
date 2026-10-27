"""The deterministic EPUB writer ``newspaper.render`` and ``transcript.render`` share (#87).

Both books are EPUB 3 with an EPUB 2 NCX: ``mimetype`` first and stored, then
``META-INF/container.xml`` and the ``OEBPS/`` files, deflated, each with a fixed time
(:data:`EPOCH`) and ``0644`` permissions, so the same input gives identical bytes. This module
holds the parts both books write the same way: escaping, the container, the package document,
the navigation and NCX, and the zip. The XHTML page shell differs (the newspaper's has a
doctype, the transcript's a direction), so each renderer keeps its own. Standard-library only.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from collections.abc import Iterable, Sequence

__all__ = [
    "EPOCH",
    "INVALID_XML",
    "EPUB_NS",
    "OEBPS",
    "XHTML_NS",
    "XHTML_TYPE",
    "XML_DECL",
    "container",
    "esc",
    "nav_body",
    "ncx",
    "opf",
    "pack",
]

EPOCH = (1980, 1, 1, 0, 0, 0)
OEBPS = "OEBPS/"
XHTML_TYPE = "application/xhtml+xml"
XHTML_NS = "http://www.w3.org/1999/xhtml"
EPUB_NS = "http://www.idpf.org/2007/ops"
XML_DECL = '<?xml version="1.0" encoding="utf-8"?>\n'
ENCODING = "utf-8"

INVALID_XML = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\ud800-\udfff￾￿]")


def esc(text: str) -> str:
    """``text`` escaped for XML text and attributes, XML-invalid characters stripped."""
    return html.escape(INVALID_XML.sub("", text), quote=True)


def container() -> str:
    return (
        f"{XML_DECL}"
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        f'<rootfiles><rootfile full-path="{OEBPS}content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>\n'
    )


def opf(
    uid: str,
    title: str,
    day: str,
    chapters: Sequence[str],
    *,
    lang: str = "en",
    direction: str | None = None,
) -> str:
    """The package document: ``chapters`` are the chapter ids (``<id>.xhtml``) in spine order.
    A ``direction`` (``ltr``/``rtl``) is set on the package and as the page progression."""
    package_dir = f' dir="{direction}"' if direction else ""
    progression = f' page-progression-direction="{direction}"' if direction else ""
    items = "".join(
        f'<item id="{cid}" href="{cid}.xhtml" media-type="{XHTML_TYPE}"/>' for cid in chapters
    )
    spine = "".join(f'<itemref idref="{cid}"/>' for cid in chapters)
    return (
        f"{XML_DECL}"
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        f'unique-identifier="uid" xml:lang="{lang}"{package_dir}>'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="uid">{esc(uid)}</dc:identifier>'
        f"<dc:title>{esc(title)}</dc:title><dc:language>{lang}</dc:language>"
        f"<dc:creator>culture-rules</dc:creator><dc:date>{day}</dc:date>"
        f'<meta property="dcterms:modified">{day}T00:00:00Z</meta></metadata>'
        f'<manifest><item id="nav" href="nav.xhtml" media-type="{XHTML_TYPE}" '
        'properties="nav"/>'
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
        f'<item id="css" href="style.css" media-type="text/css"/>{items}</manifest>'
        f'<spine toc="ncx"{progression}>{spine}</spine></package>\n'
    )


def nav_body(entries: Iterable[tuple[str, str]]) -> str:
    """The ``nav.xhtml`` body: one link per ``(label, chapter id)``."""
    links = "".join(f'<li><a href="{cid}.xhtml">{esc(label)}</a></li>' for label, cid in entries)
    return f'<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{links}</ol></nav>'


def ncx(uid: str, title: str, entries: Iterable[tuple[str, str]]) -> str:
    """The EPUB 2 NCX: one nav point per ``(label, chapter id)``, numbered from 1."""
    points = "".join(
        f'<navPoint id="np{n}" playOrder="{n}"><navLabel><text>{esc(label)}</text>'
        f'</navLabel><content src="{cid}.xhtml"/></navPoint>'
        for n, (label, cid) in enumerate(entries, 1)
    )
    return (
        f'{XML_DECL}<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{esc(uid)}"/>'
        '<meta name="dtb:depth" content="1"/><meta name="dtb:totalPageCount" content="0"/>'
        '<meta name="dtb:maxPageNumber" content="0"/></head>'
        f"<docTitle><text>{esc(title)}</text></docTitle><navMap>{points}</navMap></ncx>\n"
    )


def _write(archive: zipfile.ZipFile, name: str, data: str, compress: int) -> None:
    info = zipfile.ZipInfo(name, date_time=EPOCH)
    info.compress_type = compress
    info.external_attr = 0o644 << 16
    archive.writestr(info, data.encode(ENCODING))


def pack(files: Iterable[tuple[str, str]]) -> bytes:
    """The EPUB: ``mimetype`` first and stored, then ``(name, text)`` in order, deflated."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        _write(archive, "mimetype", "application/epub+zip", zipfile.ZIP_STORED)
        for name, data in files:
            _write(archive, name, data, zipfile.ZIP_DEFLATED)
    return buffer.getvalue()
