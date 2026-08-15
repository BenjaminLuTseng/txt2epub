"""Write a valid EPUB 3 (with EPUB 2 fallbacks) from chapters and a cover.

Hand-rolled rather than library-driven so the CJK stylesheet, the nav document
and the cover page are exactly what we want. Older readers and Kindle
conversion still look for toc.ncx and the `cover` meta, so both are included.
"""

from __future__ import annotations

import html
import uuid
import zipfile
from datetime import datetime, timezone
from io import BytesIO

CONTAINER = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

_CJK_CSS = """
body { font-family: "Songti SC", "Noto Serif CJK SC", serif; line-height: 1.8; }
p { text-indent: 2em; margin: 0; }
h1 { font-family: "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", sans-serif; }
"""

_LATIN_CSS = """
body { font-family: Georgia, "Times New Roman", serif; line-height: 1.6; }
p { text-indent: 1.3em; margin: 0; }
p.first { text-indent: 0; }
h1 { font-family: Georgia, "Times New Roman", serif; }
"""

_BASE_CSS = """@charset "UTF-8";
html { margin: 0; padding: 0; }
body {
  margin: 0 5%%;
  padding: 0;
  text-align: justify;
  -epub-hanging-punctuation: allow-end last;
  widows: 2;
  orphans: 2;
}
h1 {
  font-size: 1.5em;
  font-weight: normal;
  text-align: center;
  text-indent: 0;
  margin: 2.2em 0 1.6em 0;
  page-break-after: avoid;
  break-after: avoid;
  line-height: 1.4;
}
p + p { margin-top: 0; }
p.gap { margin-top: 1em; }
hr.sep { border: 0; border-top: 1px solid currentColor; opacity: 0.3; margin: 1.6em 25%%; }
nav[epub|type="toc"] ol { list-style: none; padding-left: 0; line-height: 2; }
nav[epub|type="toc"] a { text-decoration: none; }
section.cover { margin: 0; padding: 0; text-align: center; }
section.cover img { max-width: 100%%; height: auto; }
%(family)s
"""

_CHAPTER = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="%(lang)s" lang="%(lang)s">
<head>
  <meta charset="utf-8"/>
  <title>%(title)s</title>
  <link rel="stylesheet" type="text/css" href="../style.css"/>
</head>
<body>
<section epub:type="chapter">
<h1>%(title)s</h1>
%(body)s
</section>
</body>
</html>
"""

_COVER_PAGE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="%(lang)s" lang="%(lang)s">
<head>
  <meta charset="utf-8"/>
  <title>Cover</title>
  <meta name="viewport" content="width=1600, height=2400"/>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
<section class="cover" epub:type="cover">
  <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
       version="1.1" width="100%%" height="100%%" viewBox="0 0 1600 2400"
       preserveAspectRatio="xMidYMid meet">
    <image width="1600" height="2400" xlink:href="images/cover.png"/>
  </svg>
</section>
</body>
</html>
"""


def _e(text: str) -> str:
    return html.escape(text, quote=False)


def _paragraphs_to_html(paragraphs: list[str], latin: bool) -> str:
    out = []
    for i, para in enumerate(paragraphs):
        stripped = para.strip()
        if not stripped:
            continue
        if set(stripped) <= set("*·—-— ") and len(stripped) <= 12:
            out.append('<hr class="sep"/>')
            continue
        cls = ' class="first"' if latin and i == 0 else ""
        out.append(f"<p{cls}>{_e(stripped)}</p>")
    return "\n".join(out) or "<p/>"


def build(
    *,
    title: str,
    author: str,
    language: str,
    chapters: list,
    cover_png: bytes | None = None,
    description: str = "",
    series: str = "",
) -> bytes:
    """`chapters` is a sequence of objects with `.title` and `.paragraphs`."""
    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    latin = not language.startswith(("zh", "ja", "ko"))
    css = _BASE_CSS % {"family": _LATIN_CSS if latin else _CJK_CSS}

    files: list[tuple[str, str]] = []
    for i, chapter in enumerate(chapters, start=1):
        name = f"text/chap{i:04d}.xhtml"
        files.append((name, chapter.title))

    manifest = [
        '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '<item id="css" href="style.css" media-type="text/css"/>',
    ]
    spine = []
    if cover_png:
        manifest.append(
            '<item id="cover-image" href="images/cover.png" media-type="image/png" properties="cover-image"/>'
        )
        manifest.append('<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>')
        spine.append('<itemref idref="cover-page" linear="yes"/>')
    for i, (name, _) in enumerate(files, start=1):
        manifest.append(f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="c{i}"/>')

    meta_extra = ""
    if cover_png:
        meta_extra += '\n    <meta name="cover" content="cover-image"/>'
    if series:
        meta_extra += f'\n    <meta property="belongs-to-collection" id="series">{_e(series)}</meta>'
        meta_extra += '\n    <meta refines="#series" property="collection-type">series</meta>'
    if description:
        meta_extra += f"\n    <dc:description>{_e(description)}</dc:description>"

    opf = f"""<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid"
         xml:lang="{language}" prefix="rendition: http://www.idpf.org/vocab/rendition/#">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">{book_id}</dc:identifier>
    <dc:title>{_e(title)}</dc:title>
    <dc:language>{language}</dc:language>
    <dc:creator id="author">{_e(author or "Unknown")}</dc:creator>
    <meta refines="#author" property="role" scheme="marc:relators">aut</meta>
    <meta property="dcterms:modified">{modified}</meta>
    <meta property="rendition:layout">reflowable</meta>
    <dc:publisher>txt2epub</dc:publisher>{meta_extra}
  </metadata>
  <manifest>
    {chr(10).join("    " + m for m in manifest).strip()}
  </manifest>
  <spine toc="ncx">
    {chr(10).join("    " + s for s in spine).strip()}
  </spine>
</package>
"""

    nav_items = "\n".join(
        f'      <li><a href="{name}">{_e(chapter_title)}</a></li>' for name, chapter_title in files
    )
    nav = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      xml:lang="{language}" lang="{language}">
<head>
  <meta charset="utf-8"/>
  <title>{_e(title)}</title>
  <link rel="stylesheet" type="text/css" href="style.css"/>
</head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>目录 / Contents</h1>
    <ol>
{nav_items}
    </ol>
  </nav>
  <nav epub:type="landmarks" hidden="hidden">
    <ol>
      <li><a epub:type="bodymatter" href="{files[0][0] if files else "nav.xhtml"}">Start</a></li>
    </ol>
  </nav>
</body>
</html>
"""

    nav_points = "\n".join(
        f"""    <navPoint id="np{i}" playOrder="{i}">
      <navLabel><text>{_e(chapter_title)}</text></navLabel>
      <content src="{name}"/>
    </navPoint>"""
        for i, (name, chapter_title) in enumerate(files, start=1)
    )
    ncx = f"""<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="{language}">
  <head>
    <meta name="dtb:uid" content="{book_id}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>{_e(title)}</text></docTitle>
  <docAuthor><text>{_e(author or "Unknown")}</text></docAuthor>
  <navMap>
{nav_points}
  </navMap>
</ncx>
"""

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # The mimetype entry must be first and stored uncompressed.
        zf.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", CONTAINER)
        zf.writestr("OEBPS/content.opf", opf)
        zf.writestr("OEBPS/nav.xhtml", nav)
        zf.writestr("OEBPS/toc.ncx", ncx)
        zf.writestr("OEBPS/style.css", css)
        if cover_png:
            zf.writestr("OEBPS/images/cover.png", cover_png)
            zf.writestr("OEBPS/cover.xhtml", _COVER_PAGE % {"lang": language})
        for (name, chapter_title), chapter in zip(files, chapters):
            zf.writestr(
                f"OEBPS/{name}",
                _CHAPTER
                % {
                    "lang": language,
                    "title": _e(chapter_title),
                    "body": _paragraphs_to_html(chapter.paragraphs, latin),
                },
            )
    return buffer.getvalue()
