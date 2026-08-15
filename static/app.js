'use strict';

const $ = (id) => document.getElementById(id);
const aiReady = document.body.dataset.ai === 'yes';

let jobId = null;
let chapters = [];
let coverSpec = null;

/* ------------------------------------------------------------------ utils */

function banner(message, bad = false) {
  const el = $('banner');
  if (!message) { el.hidden = true; return; }
  el.textContent = message;
  el.className = 'banner' + (bad ? ' bad' : '');
  el.hidden = false;
}

function note(id, message, kind = '') {
  const el = $(id);
  el.textContent = message || '';
  el.className = 'note ' + kind;
}

async function api(path, options = {}) {
  const response = await fetch(path, options);
  const type = response.headers.get('content-type') || '';
  if (!type.includes('application/json')) {
    if (!response.ok) throw new Error(await response.text());
    return response;
  }
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}

const postJSON = (path, body) =>
  api(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

const fmt = (n) => n.toLocaleString();

/* ----------------------------------------------------------------- upload */

const drop = $('drop');

$('pick').addEventListener('click', (e) => { e.stopPropagation(); $('file').click(); });
drop.addEventListener('click', () => $('file').click());
$('file').addEventListener('change', (e) => { if (e.target.files[0]) upload(e.target.files[0]); });

['dragenter', 'dragover'].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); })
);
['dragleave', 'drop'].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); })
);
drop.addEventListener('drop', (e) => {
  const file = e.dataTransfer.files[0];
  if (file) upload(file);
});

async function upload(file) {
  banner('');
  drop.classList.add('busy');
  drop.querySelector('strong').textContent = 'Reading ' + file.name + '…';

  const form = new FormData();
  form.append('file', file);
  try {
    const data = await api('/api/upload', { method: 'POST', body: form });
    jobId = data.job_id;
    chapters = data.chapters;

    $('title').value = data.title;
    $('author').value = data.author;
    $('language').value = data.language;
    $('facts').innerHTML =
      `<code>${data.encoding}</code> · ${fmt(data.chars)} characters · split by <code>${data.method}</code>`;

    renderChapters();
    $('workspace').hidden = false;
    drop.hidden = true;

    if (aiReady) {
      analyze();
    } else {
      banner('No Anthropic credentials found. Title, author and chapters came from heuristics; ' +
             'the cover will be generated locally. Set ANTHROPIC_API_KEY and restart for the AI features.');
    }
  } catch (err) {
    banner(err.message, true);
  } finally {
    drop.classList.remove('busy');
    drop.querySelector('strong').textContent = 'Drop a .txt file here';
  }
}

/* ---------------------------------------------------------------- analysis */

$('analyze').addEventListener('click', analyze);

async function analyze() {
  if (!jobId) return;
  const button = $('analyze');
  button.disabled = true;
  note('meta-note', 'Reading the book…');
  try {
    const data = await postJSON('/api/analyze', { job_id: jobId });
    const meta = data.metadata || {};
    if (meta.title) $('title').value = meta.title;
    if (meta.author) $('author').value = meta.author;
    if (meta.series) $('series').value = meta.series;
    if (meta.description) $('description').value = meta.description;
    if (meta.language && meta.language !== 'other') $('language').value = meta.language;

    if (data.chapters) {
      chapters = data.chapters;
      renderChapters();
    }

    const bits = [];
    if (meta.notes) bits.push(meta.notes);
    if (data.review && data.review.note) bits.push('Chapters: ' + data.review.note);
    (data.warnings || []).forEach((w) => bits.push(w));
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

  chapters.forEach((c) => {
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
    idx.textContent = c.i + 1;

    const title = document.createElement('input');
    title.className = 'ttl';
    title.type = 'text';
    title.value = c.title;
    title.addEventListener('input', () => { c.title = title.value; });

    const len = document.createElement('span');
    len.className = 'len';
    len.textContent = fmt(c.chars);

    const eye = document.createElement('button');
    eye.className = 'eye';
    eye.title = 'Preview';
    eye.textContent = '◉';
    eye.addEventListener('click', () => peek(c.i));

    li.append(box, idx, title, len, eye);
    list.append(li);
  });
}

$('all-on').addEventListener('click', () => { chapters.forEach((c) => (c.include = true)); renderChapters(); });
$('all-off').addEventListener('click', () => { chapters.forEach((c) => (c.include = false)); renderChapters(); });

async function resplit(body) {
  try {
    const data = await postJSON('/api/resplit', Object.assign({ job_id: jobId }, body));
    chapters = data.chapters;
    renderChapters();
    banner(`Re-split into ${chapters.length} chapters (${data.method}).`);
  } catch (err) {
    banner(err.message, true);
  }
}

$('apply-regex').addEventListener('click', () => resplit({ regex: $('regex').value }));
$('auto-split').addEventListener('click', () => { $('regex').value = ''; resplit({}); });
$('single').addEventListener('click', () => resplit({ mode: 'single', title: $('title').value }));

/* ------------------------------------------------------------------- peek */

async function peek(index) {
  try {
    const data = await api(`/api/chapter?job_id=${jobId}&i=${index}`);
    $('peek-title').textContent = data.title;
    $('peek-body').innerHTML = data.paragraphs
      .map((p) => `<p>${p.replace(/[&<>]/g, (m) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[m]))}</p>`)
      .join('');
    if (data.total > data.paragraphs.length) {
      $('peek-body').innerHTML +=
        `<p class="muted">… ${fmt(data.total - data.paragraphs.length)} more paragraphs</p>`;
    }
    $('peek').hidden = false;
  } catch (err) {
    banner(err.message, true);
  }
}

$('peek-close').addEventListener('click', () => ($('peek').hidden = true));
$('peek').addEventListener('click', (e) => { if (e.target.id === 'peek') $('peek').hidden = true; });
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') $('peek').hidden = true; });

/* ------------------------------------------------------------------ cover */

$('gen-cover').addEventListener('click', async () => {
  if (!jobId) return;
  $('cover-busy').hidden = false;
  $('gen-cover').disabled = true;
  note('cover-note', '');
  try {
    const data = await postJSON('/api/cover', {
      job_id: jobId,
      title: $('title').value,
      author: $('author').value,
      description: $('description').value,
      style_hint: $('style-hint').value,
      use_ai: aiReady,
    });
    showCover(data.png, data.spec);
    note('cover-note', data.note || (data.spec.rationale || ''), data.note ? '' : 'ok');
  } catch (err) {
    note('cover-note', err.message, 'bad');
  } finally {
    $('cover-busy').hidden = true;
    $('gen-cover').disabled = false;
  }
});

function showCover(png, spec) {
  coverSpec = spec;
  const img = $('cover-img');
  img.src = png;
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
  const spec = JSON.parse(JSON.stringify(coverSpec));
  document.querySelectorAll('.swatches input[type=color]').forEach((input) => {
    spec.palette[input.dataset.key] = input.value;
  });
  spec.motif = $('motif').value;
  spec.typeface = $('typeface').value;
  spec.layout = $('layout').value;
  spec.author_line = $('author').value;
  try {
    const data = await postJSON('/api/cover/spec', { job_id: jobId, spec });
    showCover(data.png, data.spec);
  } catch (err) {
    note('cover-note', err.message, 'bad');
  }
});

$('dl-svg').addEventListener('click', () => {
  window.location = `/api/cover.svg?job_id=${jobId}`;
});

/* ------------------------------------------------------------------ build */

$('build').addEventListener('click', async () => {
  if (!jobId) return;
  const button = $('build');
  button.disabled = true;
  note('build-note', 'Assembling…');
  try {
    const response = await fetch('/api/build', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        job_id: jobId,
        title: $('title').value,
        author: $('author').value,
        series: $('series').value,
        description: $('description').value,
        language: $('language').value,
        include_cover: $('with-cover').checked,
        chapters: chapters.map((c) => ({ i: c.i, title: c.title, include: c.include })),
      }),
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(data.error || 'Build failed');
    }
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = ($('title').value || 'book').replace(/[\\/:*?"<>|]+/g, '_') + '.epub';
    a.click();
    URL.revokeObjectURL(url);
    const kept = chapters.filter((c) => c.include).length;
    note('build-note', `Done — ${kept} chapters, ${Math.round(blob.size / 1024)} KB.`, 'ok');
  } catch (err) {
    note('build-note', err.message, 'bad');
  } finally {
    button.disabled = false;
  }
});
