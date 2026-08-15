// Render a cover from a design spec, to PNG (canvas) and SVG.
// Both backends draw the same primitive list, so the two outputs cannot drift.
//
// Fonts come from the *viewer's* machine — there is no server to install them
// on. macOS has Songti SC and Hiragino Sans GB; Windows has SimSun/Microsoft
// YaHei. A machine with no CJK font at all will render tofu, which is called
// out in the UI rather than hidden.

export const WIDTH = 1600;
export const HEIGHT = 2400;
const MARGIN = 150;

const FONT_STACKS = {
  'serif-cjk': '"Songti SC", "SimSun", "Noto Serif CJK SC", "Source Han Serif SC", serif',
  'sans-cjk': '"PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif',
  'serif-latin': 'Georgia, "Times New Roman", serif',
  'sans-latin': '"Avenir Next", "Helvetica Neue", Arial, sans-serif',
};

export const hasCjk = (text) =>
  /[一-鿿぀-ヿ가-힯]/.test(text || '');

const stackFor = (typeface, cjk) => FONT_STACKS[`${typeface}-${cjk ? 'cjk' : 'latin'}`];

function hex(value, fallback) {
  let v = String(value || '').trim().replace(/^#/, '');
  if (v.length === 3) v = [...v].map((c) => c + c).join('');
  if (!/^[0-9a-f]{6}$/i.test(v)) v = fallback.replace(/^#/, '');
  return '#' + v.toLowerCase();
}

// ---------------------------------------------------------------- primitives

function motifPrimitives(motif, seed) {
  // Deterministic PRNG so the same spec always draws the same cover.
  let state = (seed >>> 0) || 1;
  const rnd = () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
  const p = [];
  const cx = WIDTH / 2;

  if (motif === 'arcs') {
    const cy = HEIGHT * 0.3;
    for (let i = 0; i < 7; i++) {
      const r = 260 + i * 145;
      const pts = [];
      for (let a = 20; a <= 160; a += 4) {
        pts.push([cx + r * Math.cos((a * Math.PI) / 180), cy + r * Math.sin((a * Math.PI) / 180)]);
      }
      p.push({ t: 'polyline', pts, w: 5 });
    }
  } else if (motif === 'rings') {
    const cy = HEIGHT * 0.42;
    for (let i = 0; i < 6; i++) p.push({ t: 'circle', c: [cx, cy], r: 190 + i * 150, w: 4 });
  } else if (motif === 'mountains') {
    const base = HEIGHT * 0.8;
    [[470, 400], [330, 300], [200, 230]].forEach(([height, step], layer) => {
      const pts = [[-60, base]];
      let x = -60;
      let up = true;
      while (x < WIDTH + 60) {
        x += step;
        pts.push([x, up ? base - height * (0.55 + 0.45 * rnd()) : base - height * 0.15]);
        up = !up;
      }
      pts.push([WIDTH + 60, base]);
      p.push({ t: 'polygon', pts, layer });
    });
  } else if (motif === 'grid') {
    for (let x = MARGIN; x <= WIDTH - MARGIN; x += 110) p.push({ t: 'line', pts: [[x, MARGIN], [x, HEIGHT - MARGIN]], w: 2 });
    for (let y = MARGIN; y <= HEIGHT - MARGIN; y += 110) p.push({ t: 'line', pts: [[MARGIN, y], [WIDTH - MARGIN, y]], w: 2 });
  } else if (motif === 'rays') {
    const oy = HEIGHT * 1.05;
    for (let i = 0; i < 17; i++) {
      const a = ((-90 + (i - 8) * 6.4) * Math.PI) / 180;
      p.push({ t: 'line', pts: [[cx, oy], [cx + 3000 * Math.cos(a), oy + 3000 * Math.sin(a)]], w: 6 });
    }
  } else if (motif === 'waves') {
    for (let i = 0; i < 9; i++) {
      const y0 = HEIGHT * 0.58 + i * 105;
      const pts = [];
      for (let x = -40; x <= WIDTH + 40; x += 20) pts.push([x, y0 + 42 * Math.sin(x / 210 + i * 0.6)]);
      p.push({ t: 'polyline', pts, w: 5 });
    }
  } else if (motif === 'bars') {
    const base = HEIGHT - MARGIN;
    let x = MARGIN;
    while (x < WIDTH - MARGIN) {
      const h = 120 + rnd() * 620;
      p.push({ t: 'rectf', xy: [x, base - h, x + 44, base] });
      x += 78;
    }
  } else if (motif === 'dots') {
    for (let y = HEIGHT * 0.1; y < HEIGHT * 0.92; y += 96)
      for (let x = MARGIN; x <= WIDTH - MARGIN; x += 96) p.push({ t: 'circle', c: [x, y], r: 7, w: 0 });
  } else if (motif === 'frame') {
    p.push({ t: 'rect', xy: [90, 90, WIDTH - 90, HEIGHT - 90], w: 6 });
    p.push({ t: 'rect', xy: [118, 118, WIDTH - 118, HEIGHT - 118], w: 2 });
  } else if (motif === 'horizon') {
    const y = HEIGHT * 0.72;
    p.push({ t: 'circle', c: [cx, y - 210], r: 240, w: 6 });
    p.push({ t: 'line', pts: [[MARGIN, y], [WIDTH - MARGIN, y]], w: 6 });
    p.push({ t: 'line', pts: [[MARGIN, y + 46], [WIDTH - MARGIN, y + 46]], w: 2 });
  }
  return p;
}

const ALPHA = { subtle: 46 / 255, medium: 92 / 255, bold: 150 / 255 };

function seedFrom(spec) {
  const s = JSON.stringify(spec.title_lines || []);
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
  return h & 0xffff;
}

// -------------------------------------------------------------------- layout

let measureCtx = null;
function measurer() {
  if (!measureCtx) measureCtx = document.createElement('canvas').getContext('2d');
  return measureCtx;
}

function layout(spec) {
  const titleLines = (spec.title_lines || []).map(String).filter((s) => s.trim()).slice(0, 3);
  if (!titleLines.length) titleLines.push('Untitled');
  const subtitle = String(spec.subtitle || '').trim();
  const author = String(spec.author_line || '').trim();
  const typeface = spec.typeface === 'sans' ? 'sans' : 'serif';
  const cjk = hasCjk(titleLines.join('') + subtitle + author);
  const stack = stackFor(typeface, cjk);

  const ctx = measurer();
  const maxW = WIDTH - 2 * MARGIN;
  let size = titleLines.length === 1 ? 250 : 200;
  while (size > 64) {
    ctx.font = `700 ${size}px ${stack}`;
    if (Math.max(...titleLines.map((l) => ctx.measureText(l).width)) <= maxW) break;
    size -= 6;
  }

  const lineGap = Math.round(size * (cjk ? 1.3 : 1.18));
  const blockH = lineGap * titleLines.length;
  const anchor = { centered: 0.4, upper: 0.22, lower: 0.6, band: 0.44 }[spec.layout] ?? 0.4;
  const top = HEIGHT * anchor - blockH / 2;

  ctx.font = `700 ${size}px ${stack}`;
  const m = ctx.measureText('M');
  const ascent = m.fontBoundingBoxAscent || m.actualBoundingBoxAscent || size * 0.8;

  const baselines = titleLines.map((_, i) => top + i * lineGap + ascent + (lineGap - size) * 0.25);
  const subSize = Math.max(40, Math.round(size * 0.26));
  const authorSize = Math.max(46, Math.round(size * 0.3));
  const ySub = subtitle ? baselines[baselines.length - 1] + lineGap * 0.72 : null;
  const ruleY = (ySub ?? baselines[baselines.length - 1]) + lineGap * 0.55;

  return {
    titleLines, subtitle, author, stack, cjk, size, subSize, authorSize,
    baselines, ySub, ruleY, yAuthor: HEIGHT * 0.885,
    band: spec.layout === 'band',
    bandBox: [0, top - lineGap * 0.45, WIDTH, ruleY + lineGap * 0.25],
  };
}

// --------------------------------------------------------------- PNG backend

export async function renderPng(spec) {
  const pal = spec.palette || {};
  const bg1 = hex(pal.background, '#1d2b36');
  const bg2 = hex(pal.background2, '#0f1a22');
  const accent = hex(pal.accent, '#c8a06a');
  const titleCol = hex(pal.title, '#f6f2e8');
  const authorCol = hex(pal.author, '#d8cfbe');

  const canvas = document.createElement('canvas');
  canvas.width = WIDTH;
  canvas.height = HEIGHT;
  const ctx = canvas.getContext('2d');

  const grad = ctx.createLinearGradient(0, 0, 0, HEIGHT);
  grad.addColorStop(0, bg1);
  grad.addColorStop(1, bg2);
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, WIDTH, HEIGHT);

  const motif = spec.motif || 'none';
  if (motif !== 'none') {
    const base = ALPHA[spec.motif_intensity] ?? ALPHA.medium;
    ctx.save();
    ctx.strokeStyle = accent;
    ctx.fillStyle = accent;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (const prim of motifPrimitives(motif, seedFrom(spec))) {
      const a = prim.layer !== undefined ? Math.min(1, base * (0.45 + 0.28 * prim.layer)) : base;
      ctx.globalAlpha = a;
      ctx.lineWidth = prim.w ?? 1;
      if (prim.t === 'line' || prim.t === 'polyline') {
        ctx.beginPath();
        prim.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.stroke();
      } else if (prim.t === 'polygon') {
        ctx.beginPath();
        prim.pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.closePath();
        ctx.fill();
      } else if (prim.t === 'circle') {
        ctx.beginPath();
        ctx.arc(prim.c[0], prim.c[1], prim.r, 0, Math.PI * 2);
        prim.w === 0 ? ctx.fill() : ctx.stroke();
      } else if (prim.t === 'rect') {
        ctx.strokeRect(prim.xy[0], prim.xy[1], prim.xy[2] - prim.xy[0], prim.xy[3] - prim.xy[1]);
      } else if (prim.t === 'rectf') {
        ctx.fillRect(prim.xy[0], prim.xy[1], prim.xy[2] - prim.xy[0], prim.xy[3] - prim.xy[1]);
      }
    }
    ctx.restore();
  }

  const lay = layout(spec);

  if (lay.band) {
    ctx.save();
    ctx.globalAlpha = 0.88;
    ctx.fillStyle = bg2;
    ctx.fillRect(lay.bandBox[0], lay.bandBox[1], lay.bandBox[2] - lay.bandBox[0], lay.bandBox[3] - lay.bandBox[1]);
    ctx.restore();
  }

  ctx.textAlign = 'center';
  ctx.textBaseline = 'alphabetic';

  ctx.fillStyle = titleCol;
  ctx.font = `700 ${lay.size}px ${lay.stack}`;
  lay.titleLines.forEach((line, i) => ctx.fillText(line, WIDTH / 2, lay.baselines[i]));

  if (lay.subtitle) {
    ctx.fillStyle = authorCol;
    ctx.font = `400 ${lay.subSize}px ${lay.stack}`;
    ctx.fillText(lay.subtitle, WIDTH / 2, lay.ySub);
  }

  const ruleW = Math.min(360, WIDTH - 2 * MARGIN);
  ctx.strokeStyle = accent;
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.moveTo((WIDTH - ruleW) / 2, lay.ruleY);
  ctx.lineTo((WIDTH + ruleW) / 2, lay.ruleY);
  ctx.stroke();

  if (lay.author) {
    ctx.fillStyle = authorCol;
    ctx.font = `400 ${lay.authorSize}px ${lay.stack}`;
    ctx.fillText(lay.author, WIDTH / 2, lay.yAuthor);
  }

  // Canvas PNG is unoptimised, and a smooth gradient is close to worst case for
  // it — a 1600x2400 cover lands around 650 KB, dwarfing the actual book. JPEG
  // handles gradients well and gets the same cover to a fraction of the size,
  // so that is what goes in the EPUB; the preview stays PNG.
  const jpeg = await new Promise((res) => canvas.toBlob(res, 'image/jpeg', 0.9));
  return {
    previewUrl: canvas.toDataURL('image/png'),
    embedBytes: new Uint8Array(await jpeg.arrayBuffer()),
    embedMime: 'image/jpeg',
  };
}

// --------------------------------------------------------------- SVG backend

const xmlEsc = (s) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export function renderSvg(spec) {
  const pal = spec.palette || {};
  const bg1 = hex(pal.background, '#1d2b36');
  const bg2 = hex(pal.background2, '#0f1a22');
  const accent = hex(pal.accent, '#c8a06a');
  const titleCol = hex(pal.title, '#f6f2e8');
  const authorCol = hex(pal.author, '#d8cfbe');
  const lay = layout(spec);
  const stack = lay.stack.replace(/"/g, "'");

  const out = [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${WIDTH}" height="${HEIGHT}" viewBox="0 0 ${WIDTH} ${HEIGHT}">`,
    `<defs><linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">` +
      `<stop offset="0" stop-color="${bg1}"/><stop offset="1" stop-color="${bg2}"/></linearGradient></defs>`,
    `<rect width="${WIDTH}" height="${HEIGHT}" fill="url(#bg)"/>`,
  ];

  const motif = spec.motif || 'none';
  if (motif !== 'none') {
    const base = ALPHA[spec.motif_intensity] ?? ALPHA.medium;
    out.push(`<g stroke="${accent}" fill="none" stroke-linecap="round" stroke-linejoin="round">`);
    for (const prim of motifPrimitives(motif, seedFrom(spec))) {
      const a = Math.round(
        (prim.layer !== undefined ? Math.min(1, base * (0.45 + 0.28 * prim.layer)) : base) * 1000,
      ) / 1000;
      if (prim.t === 'line' || prim.t === 'polyline') {
        out.push(`<polyline points="${prim.pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')}" stroke-width="${prim.w}" stroke-opacity="${a}"/>`);
      } else if (prim.t === 'polygon') {
        out.push(`<polygon points="${prim.pts.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')}" fill="${accent}" fill-opacity="${a}" stroke="none"/>`);
      } else if (prim.t === 'circle') {
        out.push(prim.w === 0
          ? `<circle cx="${prim.c[0].toFixed(1)}" cy="${prim.c[1].toFixed(1)}" r="${prim.r}" fill="${accent}" fill-opacity="${a}" stroke="none"/>`
          : `<circle cx="${prim.c[0].toFixed(1)}" cy="${prim.c[1].toFixed(1)}" r="${prim.r}" stroke-width="${prim.w}" stroke-opacity="${a}"/>`);
      } else if (prim.t === 'rect' || prim.t === 'rectf') {
        const [x0, y0, x1, y1] = prim.xy;
        const common = `x="${x0.toFixed(1)}" y="${y0.toFixed(1)}" width="${(x1 - x0).toFixed(1)}" height="${(y1 - y0).toFixed(1)}"`;
        out.push(prim.t === 'rectf'
          ? `<rect ${common} fill="${accent}" fill-opacity="${a}" stroke="none"/>`
          : `<rect ${common} stroke-width="${prim.w}" stroke-opacity="${a}"/>`);
      }
    }
    out.push('</g>');
  }

  if (lay.band) {
    const [x0, y0, x1, y1] = lay.bandBox;
    out.push(`<rect x="${x0}" y="${y0.toFixed(1)}" width="${x1 - x0}" height="${(y1 - y0).toFixed(1)}" fill="${bg2}" fill-opacity="0.88"/>`);
  }

  lay.titleLines.forEach((line, i) => {
    out.push(`<text x="${WIDTH / 2}" y="${lay.baselines[i].toFixed(1)}" text-anchor="middle" fill="${titleCol}" font-family="${stack}" font-size="${lay.size}" font-weight="700">${xmlEsc(line)}</text>`);
  });
  if (lay.subtitle) {
    out.push(`<text x="${WIDTH / 2}" y="${lay.ySub.toFixed(1)}" text-anchor="middle" fill="${authorCol}" font-family="${stack}" font-size="${lay.subSize}">${xmlEsc(lay.subtitle)}</text>`);
  }
  const ruleW = Math.min(360, WIDTH - 2 * MARGIN);
  out.push(`<line x1="${(WIDTH - ruleW) / 2}" y1="${lay.ruleY.toFixed(1)}" x2="${(WIDTH + ruleW) / 2}" y2="${lay.ruleY.toFixed(1)}" stroke="${accent}" stroke-width="5"/>`);
  if (lay.author) {
    out.push(`<text x="${WIDTH / 2}" y="${lay.yAuthor.toFixed(1)}" text-anchor="middle" fill="${authorCol}" font-family="${stack}" font-size="${lay.authorSize}">${xmlEsc(lay.author)}</text>`);
  }
  out.push('</svg>');
  return out.join('\n');
}

const SCHEMES = [
  ['#22303c', '#131d26', '#c9a227', '#f5f1e6', '#cfc6b2', 'arcs'],
  ['#2d2320', '#171110', '#c4703f', '#f6ece0', '#d8c3b0', 'horizon'],
  ['#1c2b28', '#0d1614', '#7fae9b', '#f0f5f1', '#c3d3cb', 'waves'],
  ['#2b2438', '#161122', '#b08bc4', '#f2edf7', '#cfc2dc', 'rings'],
  ['#f2ece1', '#e0d6c6', '#8c5a3c', '#2c211a', '#5c4a3a', 'frame'],
];

/** A deterministic, decent-looking spec for when no API key is configured. */
export function fallbackSpec(title, author, seed = '') {
  const key = seed || title;
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  const [bg1, bg2, accent, tcol, acol, motif] = SCHEMES[h % SCHEMES.length];

  let lines;
  if (hasCjk(title)) {
    lines = title.length <= 8 ? [title] : [title.slice(0, Math.ceil(title.length / 2)), title.slice(Math.ceil(title.length / 2))];
  } else {
    const words = title.split(/\s+/);
    lines = title.length <= 18 || words.length < 3
      ? [title]
      : [words.slice(0, Math.ceil(words.length / 2)).join(' '), words.slice(Math.ceil(words.length / 2)).join(' ')];
  }

  return {
    palette: { background: bg1, background2: bg2, accent, title: tcol, author: acol },
    layout: 'centered',
    motif,
    motif_intensity: 'subtle',
    typeface: 'serif',
    title_lines: lines,
    subtitle: '',
    author_line: author,
    rationale: 'Generated locally without the API.',
  };
}
