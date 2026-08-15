import * as ingest from './ingest.js';
import * as chap from './chapters.js';
import * as cover from './cover.js';
import * as ai from './ai.js';
import { build as buildEpub } from './epub.js';
import { deflateSupported } from './zip.js';

const $ = (id) => document.getElementById(id);
const fmt = (n) => n.toLocaleString();

let doc = null;
let chapters = [];
let coverSpec = null;
let coverImage = null;
let coverSvg = '';
let filename = '';

/* ------------------------------------------------------------------ chrome */

function banner(message, bad = false) {
  const el = $('banner');
  if (!message) return void (el.hidden = true);
  el.textContent = message;
  el.className = 'banner' + (bad ? ' bad' : '');
  el.hidden = false;
}

function note(id, message, kind = '') {
  const el = $(id);
  el.textContent = message || '';
  el.className = 'note ' + kind;
}

function refreshAiState() {
  const el = $('ai-state');
  if (ai.available()) {
    el.textContent = `Claude connected · ${ai.MODEL} · nothing but your prompts leaves this machine`;
    el.className = 'ai-state ok';
    $('open-key').textContent = 'Change API key';
  } else {
    el.textContent = 'Runs entirely in your browser — no file ever leaves this machine.';
    el.className = 'ai-state off';
    $('open-key').textContent = 'Set API key';
  }
}

/* ---------------------------------------------------------------- key modal */

$('open-key').addEventListener('click', () => {
  $('api-key').value = ai.getKey();
  note('key-note', '');
  $('keybox').hidden = false;
});
$('key-close').addEventListener('click', () => ($('keybox').hidden = true));
$('keybox').addEventListener('click', (e) => { if (e.target.id === 'keybox') $('keybox').hidden = true; });
$('key-save').addEventListener('click', () => {
  ai.setKey($('api-key').value.trim());
  refreshAiState();
  note('key-note', ai.available() ? 'Saved in this browser.' : 'Cleared.', 'ok');
  setTimeout(() => ($('keybox').hidden = true), 700);
});
$('key-clear').addEventListener('click', () => {
  ai.setKey('');
  $('api-key').value = '';
  refreshAiState();
  note('key-note', 'Cleared.', 'ok');
});

/* ----------------------------------------------------------------- reading */

const drop = $('drop');
$('pick').addEventListener('click', (e) => { e.stopPropagation(); $('file').click(); });
drop.addEventListener('click', () => $('file').click());
$('file').addEventListener('change', (e) => e.target.files[0] && open(e.target.files[0]));

['dragenter', 'dragover'].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => e.dataTransfer.files[0] && open(e.dataTransfer.files[0]));

const FILENAME_NOISE =
  /(?:\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）|全本|完本|全集|精校|校对|校對|txt|下载|下載|免费|免費|电子书|電子書)/gi;

function guessFromFilename(name) {
  let stem = name.replace(/\.[^.]+$/, '').replace(FILENAME_NOISE, ' ').trim().replace(/^[-_·—\s]+|[-_·—\s]+$/g, '');
  const book = /《([^》]+)》\s*(.*)/.exec(stem);
  if (book) return [book[1].trim(), book[2].replace(/^[-_\s by作者：:]+/, '').trim()];
  for (const sep of [' - ', ' – ', ' — ', '_by_', ' by ', '－', '—', '-', '_']) {
    const i = stem.indexOf(sep);
    if (i > 0) {
      const left = stem.slice(0, i).trim();
      const right = stem.slice(i + sep.length).trim();
      if (left && right && right.length <= 30) return [left, right];
    }
  }
  return [stem || 'Untitled', ''];
}

async function open(file) {
  banner('');
  filename = file.name;
  drop.classList.add('busy');
  drop.querySelector('strong').textContent = `Reading ${file.name}…`;
  try {
    const bytes = new Uint8Array(await file.arrayBuffer());
    doc = ingest.load(bytes);
    if (!doc.text.trim()) throw new Error('No readable text found in that file.');

    const result = chap.split(doc.lines, null, doc.language);
    chapters = result.chapters;

    const [title, author] = guessFromFilename(file.name);
    $('title').value = title;
    $('author').value = author;
    $('language').value = doc.language;
    $('facts').innerHTML =
      `<code>${doc.encoding}</code> · ${fmt(doc.charCount)} characters · split by <code>${result.method}</code>`;
    $('diag-line').textContent =
      'Pattern hits — ' +
      chap.PATTERNS.map(([n, p]) => `${n}: ${chap.candidateLines(doc.lines, p).length}`).join(', ');

    renderChapters();
    $('workspace').hidden = false;
    drop.hidden = true;

    if (ai.available()) analyze();
    else banner('No API key set, so title, author and chapters came from heuristics and the cover will be generated locally. Everything still works — set a key from the header for the Claude features.');
  } catch (err) {
    banner(err.message, true);
  } finally {
    drop.classList.remove('busy');
    drop.querySelector('strong').textContent = 'Drop a .txt file here';
  }
}

/* --------------------------------------------------------------- analysis */

$('analyze').addEventListener('click', analyze);

async function analyze() {
  if (!doc) return;
  if (!ai.available()) return note('meta-note', 'Set an API key first (header, top right).', 'bad');
  const button = $('analyze');
  button.disabled = true;
  note('meta-note', 'Reading the book…');
  try {
    const headings = chapters.map((c) => c.title);
    const meta = await ai.detectMetadata({
      filename,
      head: doc.text.slice(0, 4000),
      tail: doc.text.slice(-1500),
      headings,
    });
    if (meta.title) $('title').value = meta.title;
    if (meta.author) $('author').value = meta.author;
    if (meta.series) $('series').value = meta.series;
    if (meta.description) $('description').value = meta.description;
    if (meta.language && meta.language !== 'other') $('language').value = meta.language;

    const bits = [];
    if (meta.notes) bits.push(meta.notes);

    const headingSet = new Set(headings);
    const body = doc.lines.filter((l) => l.trim() && !headingSet.has(l.trim()));
    const step = Math.max(1, Math.floor(body.length / 25));
    const sampleLines = body.filter((_, i) => i % step === 0).slice(0, 25);

    try {
      const review = await ai.reviewChapters({
        method: 'auto', headings, sampleLines, total: chapters.length,
      });
      if (review.note) bits.push('Chapters: ' + review.note);
      if (review.verdict === 'use_regex' && review.regex) {
        try {
          const result = chap.split(doc.lines, review.regex, doc.language);
          if (result.chapters.length > 1 && result.chapters.length <= 5000) {
            chapters = result.chapters;
            renderChapters();
          } else {
            bits.push(`Claude's regex produced ${result.chapters.length} chapters; kept the original split.`);
          }
        } catch (e) {
          bits.push(`Claude suggested an invalid regex (${e.message}); kept the original split.`);
        }
      }
    } catch (e) {
      bits.push(`Split not reviewed (${e.message}).`);
    }

    note('meta-note', bits.join(' · '), meta.confidence === 'low' ? 'bad' : 'ok');
  } catch (err) {
    note('meta-note', err.message, 'bad');
  } finally {
    button.disabled = false;
  }
}

/* ---------------------------------------------------------------- chapters */

function renderChapters() {
  const list = $('chapters');
  list.innerHTML = '';
  $('chap-count').textContent = chapters.length;

  chapters.forEach((c, index) => {
    const li = document.createElement('li');
    li.className = c.include ? '' : 'off';

    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = c.include;
    box.addEventListener('change', () => {
      c.include = box.checked;
      li.className = box.checked ? '' : 'off';
    });

    const idx = document.createElement('span');
    idx.className = 'idx';
    idx.textContent = index + 1;

    const title = document.createElement('input');
    title.className = 'ttl';
    title.type = 'text';
    title.value = c.title;
    title.addEventListener('input', () => { c.title = title.value; });

    const len = document.createElement('span');
    len.className = 'len';
    len.textContent = fmt(c.charCount);

    const eye = document.createElement('button');
    eye.className = 'eye';
    eye.title = 'Preview';
    eye.textContent = '◉';
    eye.addEventListener('click', () => peek(c));

    li.append(box, idx, title, len, eye);
    list.append(li);
  });
}

$('all-on').addEventListener('click', () => { chapters.forEach((c) => (c.include = true)); renderChapters(); });
$('all-off').addEventListener('click', () => { chapters.forEach((c) => (c.include = false)); renderChapters(); });

function resplit(regex, single) {
  try {
    if (single) {
      chapters = [new chap.Chapter($('title').value || 'Full Text', chap.toParagraphs(doc.lines))];
      renderChapters();
      return banner('Re-split into a single chapter.');
    }
    const result = chap.split(doc.lines, regex || null, doc.language);
    if (!result.chapters.length) return banner('That pattern matched nothing usable.', true);
    chapters = result.chapters;
    renderChapters();
    banner(`Re-split into ${chapters.length} chapters (${result.method}).`);
  } catch (err) {
    banner(`Invalid regular expression: ${err.message}`, true);
  }
}

$('apply-regex').addEventListener('click', () => resplit($('regex').value, false));
$('auto-split').addEventListener('click', () => { $('regex').value = ''; resplit(null, false); });
$('single').addEventListener('click', () => resplit(null, true));

/* ------------------------------------------------------------------- peek */

function peek(c) {
  $('peek-title').textContent = c.title;
  const shown = c.paragraphs.slice(0, 60);
  $('peek-body').innerHTML =
    shown.map((p) => `<p>${p.replace(/[&<>]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' })[m])}</p>`).join('') +
    (c.paragraphs.length > shown.length
      ? `<p class="muted">… ${fmt(c.paragraphs.length - shown.length)} more paragraphs</p>` : '');
  $('peek').hidden = false;
}

$('peek-close').addEventListener('click', () => ($('peek').hidden = true));
$('peek').addEventListener('click', (e) => { if (e.target.id === 'peek') $('peek').hidden = true; });
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { $('peek').hidden = true; $('keybox').hidden = true; }
});

/* ------------------------------------------------------------------ cover */

$('gen-cover').addEventListener('click', async () => {
  if (!doc) return;
  $('cover-busy').hidden = false;
  $('gen-cover').disabled = true;
  note('cover-note', '');
  const title = $('title').value || 'Untitled';
  const author = $('author').value;
  let spec = null;
  let message = '';
  try {
    if (ai.available()) {
      try {
        spec = await ai.designCover({
          title, author, language: doc.language,
          description: $('description').value,
          opening: doc.text.slice(0, 1500),
          styleHint: $('style-hint').value,
        });
      } catch (err) {
        message = `Designed locally — ${err.message}`;
      }
    }
    if (!spec) {
      spec = cover.fallbackSpec(title, author, $('style-hint').value || title);
      message = message || 'Designed locally (no API key set).';
    }
    spec.author_line = author;
    if (!spec.title_lines?.length) spec.title_lines = [title];
    await showCover(spec);
    note('cover-note', message || spec.rationale || '', message ? '' : 'ok');
  } catch (err) {
    note('cover-note', err.message, 'bad');
  } finally {
    $('cover-busy').hidden = true;
    $('gen-cover').disabled = false;
  }
});

async function showCover(spec) {
  coverSpec = spec;
  const raster = await cover.renderPng(spec);
  coverImage = { bytes: raster.embedBytes, mime: raster.embedMime };
  coverSvg = cover.renderSvg(spec);

  const img = $('cover-img');
  img.src = raster.previewUrl;
  img.classList.add('on');
  $('cover-empty').hidden = true;
  $('dl-svg').disabled = false;
  $('cover-tweak').hidden = false;

  document.querySelectorAll('.swatches input[type=color]').forEach((input) => {
    input.value = spec.palette[input.dataset.key] || '#000000';
  });
  $('motif').value = spec.motif;
  $('typeface').value = spec.typeface;
  $('layout').value = spec.layout;
}

$('rerender').addEventListener('click', async () => {
  if (!coverSpec) return;
  const spec = structuredClone(coverSpec);
  document.querySelectorAll('.swatches input[type=color]').forEach((input) => {
    spec.palette[input.dataset.key] = input.value;
  });
  spec.motif = $('motif').value;
  spec.typeface = $('typeface').value;
  spec.layout = $('layout').value;
  spec.author_line = $('author').value;
  await showCover(spec);
});

function download(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$('dl-svg').addEventListener('click', () =>
  download(new Blob([coverSvg], { type: 'image/svg+xml' }), 'cover.svg'));

/* ------------------------------------------------------------------ build */

$('build').addEventListener('click', async () => {
  if (!doc) return;
  const button = $('build');
  button.disabled = true;
  note('build-note', 'Assembling…');
  try {
    const selected = chapters.filter((c) => c.include);
    if (!selected.length) throw new Error('Every chapter is excluded — nothing to build.');
    const title = $('title').value || 'Untitled';
    const blob = await buildEpub({
      title,
      author: $('author').value,
      language: $('language').value,
      chapters: selected,
      coverImage: $('with-cover').checked ? coverImage : null,
      description: $('description').value,
      series: $('series').value,
    });
    download(blob, title.replace(/[\\/:*?"<>|]+/g, '_') + '.epub');
    note('build-note',
      `Done — ${selected.length} chapters, ${Math.round(blob.size / 1024)} KB.` +
      (deflateSupported ? '' : ' (Stored uncompressed: this browser has no CompressionStream.)'),
      'ok');
  } catch (err) {
    note('build-note', err.message, 'bad');
  } finally {
    button.disabled = false;
  }
});

refreshAiState();
