// EPUB 3 packaging, with EPUB 2 fallbacks (toc.ncx, the `cover` meta) so older
// readers and Kindle conversion still work.

import { makeZip } from './zip.js';

const CONTAINER = `<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
`;

const CJK_CSS = `
body { font-family: "Songti SC", "Noto Serif CJK SC", serif; line-height: 1.8; }
p { text-indent: 2em; margin: 0; }
h1 { font-family: "PingFang SC", "Hiragino Sans GB", "Noto Sans CJK SC", sans-serif; }
`;

const LATIN_CSS = `
body { font-family: Georgia, "Times New Roman", serif; line-height: 1.6; }
p { text-indent: 1.3em; margin: 0; }
p.first { text-indent: 0; }
h1 { font-family: Georgia, "Times New Roman", serif; }
`;

const baseCss = (family) => `@charset "UTF-8";
html { margin: 0; padding: 0; }
body {
  margin: 0 5%;
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
hr.sep { border: 0; border-top: 1px solid currentColor; opacity: 0.3; margin: 1.6em 25%; }
section.cover { margin: 0; padding: 0; text-align: center; }
${family}`;

const esc = (s) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function paragraphsToHtml(paragraphs, latin) {
  const out = [];
  paragraphs.forEach((para, i) => {
    const s = para.trim();
    if (!s) return;
    if (s.length <= 12 && /^[*·—\-— ]+$/.test(s)) {
      out.push('<hr class="sep"/>');
      return;
    }
    out.push(`<p${latin && i === 0 ? ' class="first"' : ''}>${esc(s)}</p>`);
  });
  return out.join('\n') || '<p/>';
}

function uuid() {
  return crypto.randomUUID
    ? crypto.randomUUID()
    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
        const r = (Math.random() * 16) | 0;
        return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
      });
}

/**
 * @param {{title, author, language, chapters, coverPng?: Uint8Array,
 *          description?: string, series?: string}} opts
 * @returns {Promise<Blob>}
 */
export async function build({
  title,
  author,
  language,
  chapters,
  coverImage = null,
  description = '',
  series = '',
}) {
  const coverExt = coverImage?.mime === 'image/jpeg' ? 'jpg' : 'png';
  const coverHref = `images/cover.${coverExt}`;
  const bookId = `urn:uuid:${uuid()}`;
  const modified = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
  const latin = !/^(zh|ja|ko)/.test(language);
  const css = baseCss(latin ? LATIN_CSS : CJK_CSS);

  const files = chapters.map((c, i) => ({
    name: `text/chap${String(i + 1).padStart(4, '0')}.xhtml`,
    title: c.title,
    chapter: c,
  }));

  const manifest = [
    '<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
    '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
    '<item id="css" href="style.css" media-type="text/css"/>',
  ];
  const spine = [];
  if (coverImage) {
    manifest.push(
      `<item id="cover-image" href="${coverHref}" media-type="${coverImage.mime}" properties="cover-image"/>`,
      '<item id="cover-page" href="cover.xhtml" media-type="application/xhtml+xml"/>',
    );
    spine.push('<itemref idref="cover-page" linear="yes"/>');
  }
  files.forEach((f, i) => {
    manifest.push(`<item id="c${i + 1}" href="${f.name}" media-type="application/xhtml+xml"/>`);
    spine.push(`<itemref idref="c${i + 1}"/>`);
  });

  let metaExtra = '';
  if (coverImage) metaExtra += '\n    <meta name="cover" content="cover-image"/>';
  if (series) {
    metaExtra += `\n    <meta property="belongs-to-collection" id="series">${esc(series)}</meta>`;
    metaExtra += '\n    <meta refines="#series" property="collection-type">series</meta>';
  }
  if (description) metaExtra += `\n    <dc:description>${esc(description)}</dc:description>`;

  const opf = `<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid"
         xml:lang="${language}" prefix="rendition: http://www.idpf.org/vocab/rendition/#">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="bookid">${bookId}</dc:identifier>
    <dc:title>${esc(title)}</dc:title>
    <dc:language>${language}</dc:language>
    <dc:creator id="author">${esc(author || 'Unknown')}</dc:creator>
    <meta refines="#author" property="role" scheme="marc:relators">aut</meta>
    <meta property="dcterms:modified">${modified}</meta>
    <meta property="rendition:layout">reflowable</meta>
    <dc:publisher>txt2epub</dc:publisher>${metaExtra}
  </metadata>
  <manifest>
    ${manifest.join('\n    ')}
  </manifest>
  <spine toc="ncx">
    ${spine.join('\n    ')}
  </spine>
</package>
`;

  const nav = `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"
      xml:lang="${language}" lang="${language}">
<head><meta charset="utf-8"/><title>${esc(title)}</title>
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
  <nav epub:type="toc" id="toc">
    <h1>目录 / Contents</h1>
    <ol>
${files.map((f) => `      <li><a href="${f.name}">${esc(f.title)}</a></li>`).join('\n')}
    </ol>
  </nav>
  <nav epub:type="landmarks" hidden="hidden">
    <ol><li><a epub:type="bodymatter" href="${files[0]?.name || 'nav.xhtml'}">Start</a></li></ol>
  </nav>
</body>
</html>
`;

  const ncx = `<?xml version="1.0" encoding="UTF-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="${language}">
  <head>
    <meta name="dtb:uid" content="${bookId}"/>
    <meta name="dtb:depth" content="1"/>
    <meta name="dtb:totalPageCount" content="0"/>
    <meta name="dtb:maxPageNumber" content="0"/>
  </head>
  <docTitle><text>${esc(title)}</text></docTitle>
  <docAuthor><text>${esc(author || 'Unknown')}</text></docAuthor>
  <navMap>
${files
  .map(
    (f, i) => `    <navPoint id="np${i + 1}" playOrder="${i + 1}">
      <navLabel><text>${esc(f.title)}</text></navLabel>
      <content src="${f.name}"/>
    </navPoint>`,
  )
  .join('\n')}
  </navMap>
</ncx>
`;

  const entries = [
    // Must be first and stored uncompressed.
    { name: 'mimetype', data: 'application/epub+zip', store: true },
    { name: 'META-INF/container.xml', data: CONTAINER },
    { name: 'OEBPS/content.opf', data: opf },
    { name: 'OEBPS/nav.xhtml', data: nav },
    { name: 'OEBPS/toc.ncx', data: ncx },
    { name: 'OEBPS/style.css', data: css },
  ];

  if (coverImage) {
    // Already compressed; deflating again only costs CPU.
    entries.push({ name: `OEBPS/${coverHref}`, data: coverImage.bytes, store: true });
    entries.push({
      name: 'OEBPS/cover.xhtml',
      data: `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="${language}" lang="${language}">
<head><meta charset="utf-8"/><title>Cover</title>
<meta name="viewport" content="width=1600, height=2400"/>
<link rel="stylesheet" type="text/css" href="style.css"/></head>
<body>
<section class="cover" epub:type="cover">
  <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"
       version="1.1" width="100%" height="100%" viewBox="0 0 1600 2400"
       preserveAspectRatio="xMidYMid meet">
    <image width="1600" height="2400" xlink:href="${coverHref}"/>
  </svg>
</section>
</body>
</html>
`,
    });
  }

  for (const f of files) {
    entries.push({
      name: `OEBPS/${f.name}`,
      data: `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="${language}" lang="${language}">
<head><meta charset="utf-8"/><title>${esc(f.title)}</title>
<link rel="stylesheet" type="text/css" href="../style.css"/></head>
<body>
<section epub:type="chapter">
<h1>${esc(f.title)}</h1>
${paragraphsToHtml(f.chapter.paragraphs, latin)}
</section>
</body>
</html>
`,
    });
  }

  return makeZip(entries);
}
