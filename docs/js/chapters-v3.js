// Split a plain-text book into chapters.
//
// Every 第X章 marker is a chapter boundary. Markers may be embedded in a line
// and may have release labels before them; those labels are ignored.

const CN_NUM = '零一二三四五六七八九十百千两〇壹贰叁肆伍陆柒捌玖拾０-９0-9';
export const MAX_HEADING_LEN = 60;

// Real-world headings often carry a section marker or brackets:
//   正文 第一章 初雪   /   【第一章】初雪   /   ★第一章 初雪
const PREFIX = '(?:[【\\[（(★☆◆◇※·\\-—=＝\\s　]*(?:正文|VIP|vip|卷[一二三四五六七八九十0-9]*)?[】\\]）)\\s　]*)?';
const TAIL = '[ \\t　]*[:：、.．\\-—~～]?[ \\t　]*(?<title>.{0,50}?)[】\\]）)]?$';

export const PATTERNS = [
  ['markdown', /^#{1,3}[ \t]+(?<title>\S.*)$/u],
  [
    // The definitive Chinese chapter unit. 章/回/節/折 only — volumes are a
    // separate, coarser level and must not dilute this family's count.
    'cn_chapter',
    new RegExp(`^${PREFIX}第[ \\t]*[${CN_NUM}]{1,12}[ \\t]*[章回節节折](?![\\u4e00-\\u9fff])${TAIL}`, 'u'),
  ],
  [
    'cn_volume',
    new RegExp(
      `^${PREFIX}(?:第[ \\t]*[${CN_NUM}]{1,12}[ \\t]*[卷篇部集幕]` +
        `|卷[ \\t]*[${CN_NUM}]{1,6})(?![\\u4e00-\\u9fff])${TAIL}`,
      'u',
    ),
  ],
  [
    'en_chapter',
    /^(?:chapter|part|book|section)[ \t]+(?:\d{1,4}|[ivxlcdm]{1,8}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\b[ \t]*[:.\-—]?[ \t]*(?<title>.{0,60})$/iu,
  ],
  ['bare_number', /^(?<num>\d{1,4})[ \t]*[、.．:：]?[ \t　]*(?<title>.{0,50})$/u],
];

export const SPECIAL =
  /^(?:序[章言幕曲]?|楔子|引子|引言|前言|自序|後記|后记|尾聲|尾声|終章|终章|番外.{0,20}|附錄|附录|作者的話|作者的话|prologue|epilogue|foreword|preface|afterword|introduction|appendix)[ \t　]*[:：、.\-—]?[ \t　]*(.{0,50})$/iu;

export class Chapter {
  constructor(title, paragraphs) {
    this.title = title;
    this.paragraphs = paragraphs;
    this.include = true;
  }
  get charCount() {
    return this.paragraphs.reduce((n, p) => n + p.length, 0);
  }
  get preview() {
    return this.paragraphs.join(' ').slice(0, 180);
  }
}

export function candidateLines(lines, pattern) {
  const hits = [];
  for (let i = 0; i < lines.length; i++) {
    const s = lines[i].trim();
    if (!s || s.length > MAX_HEADING_LEN) continue;
    if (pattern.test(s)) hits.push(i);
  }
  return hits;
}

function ascending(lines, idxs, pattern) {
  const nums = [];
  for (const i of idxs) {
    const m = pattern.exec(lines[i].trim());
    if (m?.groups?.num) nums.push(parseInt(m.groups.num, 10));
  }
  if (nums.length < 3) return false;
  let up = 0;
  for (let i = 1; i < nums.length; i++) if (nums[i] > nums[i - 1]) up++;
  return up / (nums.length - 1) > 0.9;
}

export function findHeadings(lines, customRegex) {
  if (customRegex) {
    return { idxs: candidateLines(lines, new RegExp(customRegex, 'iu')), method: 'custom' };
  }

  const found = {};
  for (const [name, pattern] of PATTERNS) {
    const hits = candidateLines(lines, pattern);
    if (hits.length < 2) continue;
    if (name === 'bare_number' && !ascending(lines, hits, pattern)) continue;
    // 第…章 is specific enough to trust at almost any density — a densely
    // chaptered novel is a real thing. The loose families need a tight ceiling.
    const ceiling = name === 'cn_chapter' || name === 'cn_volume' ? 0.9 : 0.25;
    if (hits.length > Math.max(40, lines.length * ceiling)) continue;
    found[name] = hits;
  }

  let method = '';
  let best = [];
  if ((found.cn_chapter || []).length >= 2) {
    method = 'cn_chapter';
    best = found.cn_chapter;
  } else {
    for (const [name, hits] of Object.entries(found)) {
      if (hits.length > best.length) {
        method = name;
        best = hits;
      }
    }
  }

  // Once 第…章 exists, it is the only Chinese chapter signal. Do not add
  // release labels such as 卷 or 序 as competing chapter entries.
  const extra = method === 'cn_chapter' ? new Set() : new Set(candidateLines(lines, SPECIAL));
  const merged = [...new Set([...best, ...extra])].sort((a, b) => a - b);
  return { idxs: merged, method: merged.length ? method || 'special_only' : 'none' };
}

const TERMINAL = '。．.！!？?”』」…—〉》';

function runsSuggestWrapping(body) {
  const filled = body.map((l) => l.trim()).filter(Boolean);
  if (filled.length < 5) return false;

  // Chinese/Japanese .txt: every line is a complete paragraph, ending on
  // sentence-final punctuation. Gutenberg-style English breaks mid-sentence.
  const finished = filled.filter((l) => TERMINAL.includes(l[l.length - 1])).length;
  if (finished / filled.length >= 0.6) return false;

  const runs = [];
  let current = 0;
  for (const line of body) {
    if (line.trim()) current++;
    else if (current) {
      runs.push(current);
      current = 0;
    }
  }
  if (current) runs.push(current);
  if (!runs.length || runs.reduce((a, b) => a + b, 0) / runs.length <= 1.6) return false;

  const lengths = filled.map((l) => l.length).sort((a, b) => a - b);
  const wrapWidth = lengths[Math.floor(lengths.length * 0.95)];
  if (wrapWidth > 120) return false; // paragraph-length lines, not a wrap column
  return lengths.filter((n) => n >= wrapWidth * 0.8).length / lengths.length > 0.5;
}

export function toParagraphs(body) {
  if (runsSuggestWrapping(body)) {
    const paragraphs = [];
    let buffer = [];
    for (const line of body) {
      if (line.trim()) buffer.push(line.trim());
      else if (buffer.length) {
        paragraphs.push(buffer.join(' '));
        buffer = [];
      }
    }
    if (buffer.length) paragraphs.push(buffer.join(' '));
    return paragraphs;
  }
  return body.map((l) => l.trim()).filter(Boolean);
}

function cnNumber(value) {
  if (/^[0-9０-９]+$/u.test(value)) return Number(value.replace(/[０-９]/gu, (c) => String(c.charCodeAt(0) - 0xff10)));
  const digits = new Map([...`零〇一二兩两三四五六七八九`].map((c, i) => [c, [0, 0, 1, 2, 2, 2, 3, 4, 5, 6, 7, 8, 9][i]]));
  const units = new Map([['十', 10], ['拾', 10], ['百', 100], ['千', 1000]]);
  let section = 0; let number = 0;
  for (const c of value) {
    if (digits.has(c)) number = digits.get(c);
    else if (units.has(c)) { section += (number || 1) * units.get(c); number = 0; }
    else return null;
  }
  return section + number;
}

function repairEmbeddedFirstHeading(lines) {
  const repaired = [];
  for (const line of lines) {
    const matches = [...line.matchAll(new RegExp(`第(?<num>[${CN_NUM}]{1,12})章`, 'gu'))];
    if (!matches.length) {
      repaired.push(line);
      continue;
    }
    let cursor = 0;
    for (const match of matches) {
      const before = line.slice(cursor, match.index);
      if (before.trim()) repaired.push(before);
      repaired.push(`第${match.groups.num}章`);
      cursor = match.index + match[0].length;
    }
    const after = line.slice(cursor);
    if (after.trim()) repaired.push(after);
  }
  return repaired;
}

/** Discard a contents listing at the head of the file — very common in
 *  Chinese .txt releases, where every chapter title is printed as a bare list. */
function dropTocBlock(chapters) {
  const seen = new Map();
  for (const c of chapters) seen.set(c.title, (seen.get(c.title) || 0) + 1);

  let run = 0;
  for (const c of chapters) {
    // Requiring the title to reappear later keeps genuine empty dividers
    // (第一卷 with no text of its own) while still removing the listing.
    if (c.charCount < 30 && (seen.get(c.title) || 0) > 1) run++;
    else break;
  }
  return run >= 5 && run < chapters.length
    ? { chapters: chapters.slice(run), dropped: run }
    : { chapters, dropped: 0 };
}

export function split(lines, customRegex, language = 'en') {
  const cjk = /^(zh|ja|ko)/.test(language);
  const workingLines = repairEmbeddedFirstHeading(lines);
  const { idxs, method: found } = findHeadings(workingLines, customRegex);
  let method = found;

  if (!idxs.length) {
    const body = toParagraphs(workingLines);
    return {
      chapters: body.length ? [new Chapter(cjk ? '正文' : 'Full Text', body)] : [],
      method: 'none',
    };
  }

  let chapters = [];
  const front = toParagraphs(workingLines.slice(0, idxs[0]));
  if (front.length && front.reduce((n, p) => n + p.length, 0) > 80) {
    chapters.push(new Chapter(cjk ? '前言' : 'Front Matter', front));
  }

  const bounds = [...idxs, workingLines.length];
  for (let i = 0; i < bounds.length - 1; i++) {
    const start = bounds[i];
    const title = workingLines[start].trim().replace(/^#+/, '').trim();
    chapters.push(new Chapter(title || '(untitled)', toParagraphs(workingLines.slice(start + 1, bounds[i + 1]))));
  }

  const trimmed = dropTocBlock(chapters);
  if (trimmed.dropped) method += ` (dropped ${trimmed.dropped}-entry contents list)`;

  // Keep front matter in place, but force all 第X章 entries into numeric order.
  const output = trimmed.chapters;
  const numbered = output
    .map((chapter, index) => {
      const match = /^第(?<num>[零〇一二兩两三四五六七八九十百千壹贰叁肆伍陆柒捌玖拾０-９0-9]{1,12})章$/u.exec(chapter.title);
      return match ? { index, number: cnNumber(match.groups.num), chapter } : null;
    })
    .filter((item) => item && item.number != null);
  if (numbered.length >= 2) {
    const sorted = [...numbered].sort((a, b) => a.number - b.number);
    numbered.forEach((item, index) => { output[item.index] = sorted[index].chapter; });
  }
  return { chapters: output, method };
}
