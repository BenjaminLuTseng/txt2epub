// Decode a .txt of unknown encoding, then normalise it.
//
// Browsers ship WHATWG decoders for gb18030, big5, shift_jis and euc-kr, so the
// hard part is choosing between them. Same approach as the Python version:
// decode with each candidate and score by how *ordinary* the characters are.
// Real Chinese leans on a few hundred common characters; a wrong CJK codec
// produces rare-ideograph soup.

const COMMON_SIMPLIFIED =
  '的一是了我不人在他有这个上们来到时大地为子中你说生国年着就那和要她出也得里后自以' +
  '会家可下而过天去能对小多然于心学么之都好看起发当没成只如事把还用第样道想作种开美' +
  '总从无情己面最女但现前些所同日手又行意动方期它头经长儿回位分爱老因很给名法间知世' +
  '什两次使身者被高已亲其进此话常与活正感';
const COMMON_TRADITIONAL =
  '們這來時個國學會後說對於發過麼萬點爲產經體實現關樣頭長無愛親車東馬語話買賣讀寫聽' +
  '見開門問題還與從當隻總處務準辦種義許將軍書氣聲區內兩戰師應該沒覺得裡邊變傅劍靈魔' +
  '勢陣訣煉輩隨術龍鳳識脈遠處';
const COMMON = new Set([...COMMON_SIMPLIFIED, ...COMMON_TRADITIONAL]);

const TRAD_ONLY = new Set('們這來時國學會後說對發過麼萬點產經體實現關樣頭長無愛親車東馬語話買賣讀寫聽見開門問題還與從個');
const SIMP_ONLY = new Set('们这来时国学会后说对发过么万点产经体实现关样头长无爱亲车东马语话买卖读写听见开门问题还与从个');

// Ordered by coverage. gb18030 decodes almost anything, so it never wins on
// decodability alone — only on score.
const CANDIDATES = [
  'utf-8',
  'gb18030',
  'big5',
  'utf-16le',
  'utf-16be',
  'shift_jis',
  'euc-kr',
  'windows-1252',
];

const SAMPLE_BYTES = 400_000;

const isHan = (ch) => ch >= '一' && ch <= '鿿';

function score(text) {
  const n = text.length;
  if (!n) return -Infinity;
  let cjk = 0;
  let common = 0;
  let bad = 0;
  let asciiOk = 0;
  for (const ch of text) {
    if (isHan(ch)) {
      cjk++;
      if (COMMON.has(ch)) common++;
    } else if (COMMON.has(ch)) {
      common++;
    }
    if (ch === '\n' || ch === '\r' || ch === '\t') {
      asciiOk++;
      continue;
    }
    const code = ch.codePointAt(0);
    // C0/C1 controls, replacement char, private use, surrogates.
    if (code < 0x20 || (code >= 0x7f && code <= 0x9f) || code === 0xfffd ||
        (code >= 0xe000 && code <= 0xf8ff)) {
      bad++;
    } else if (ch >= ' ' && ch <= '~') {
      asciiOk++;
    }
  }
  let s = -60 * (bad / n);
  if (cjk / n > 0.03) s += 100 * (common / cjk);
  else s += 45 * (asciiOk / n);
  return s;
}

/**
 * Decode a prefix, tolerating a character split by the cut.
 *
 * Truncating a byte sample almost always lands mid-character in a multibyte
 * encoding. A non-streaming fatal decode then throws, the *correct* codec is
 * discarded, and scoring is left choosing between codecs that can never fail
 * (windows-1252 decodes anything) — so Chinese text ends up as mojibake.
 * `{stream: true}` holds the dangling tail instead of throwing, while a codec
 * that genuinely cannot read the content still throws.
 */
function decodeSample(bytes, label) {
  try {
    return new TextDecoder(label, { fatal: true }).decode(
      bytes.subarray(0, SAMPLE_BYTES),
      { stream: true },
    );
  } catch {
    return null;
  }
}

export function scoreCandidates(bytes) {
  return CANDIDATES.map((label) => {
    const text = decodeSample(bytes, label);
    return text === null
      ? { encoding: label, score: -Infinity, sample: '<cannot decode this content>' }
      : { encoding: label, score: Math.round(score(text) * 100) / 100,
          sample: text.slice(0, 120).replace(/\n/g, '⏎') };
  }).sort((a, b) => b.score - a.score);
}

const BOMS = [
  [[0xef, 0xbb, 0xbf], 'utf-8'],
  [[0xff, 0xfe, 0x00, 0x00], 'utf-16le'],
  [[0xff, 0xfe], 'utf-16le'],
  [[0xfe, 0xff], 'utf-16be'],
];

function startsWith(bytes, prefix) {
  return prefix.every((b, i) => bytes[i] === b);
}

export function decode(bytes) {
  for (const [prefix, label] of BOMS) {
    if (startsWith(bytes, prefix)) {
      return { text: new TextDecoder(label).decode(bytes), encoding: label + ' (BOM)' };
    }
  }

  // A clean strict UTF-8 decode is decisive: no other codec produces valid
  // UTF-8 multibyte sequences by accident at any length.
  try {
    return { text: new TextDecoder('utf-8', { fatal: true }).decode(bytes), encoding: 'utf-8' };
  } catch { /* not UTF-8 */ }

  // BOM-less UTF-16 is unmistakable from NUL placement.
  const head = bytes.subarray(0, 8000);
  let nul = 0;
  let even = 0;
  let odd = 0;
  for (let i = 0; i < head.length; i++) {
    if (head[i] === 0) {
      nul++;
      if (i % 2 === 0) even++;
      else odd++;
    }
  }
  if (nul > head.length * 0.2) {
    const label = even > odd ? 'utf-16be' : 'utf-16le';
    return { text: new TextDecoder(label).decode(bytes), encoding: label + ' (no BOM)' };
  }

  let best = null;
  for (const label of CANDIDATES) {
    const text = decodeSample(bytes, label);
    if (text === null) continue;
    const s = score(text);
    if (!best || s > best.score) best = { score: s, label };
  }
  if (!best) return { text: new TextDecoder('utf-8').decode(bytes), encoding: 'utf-8 (with errors)' };
  return { text: new TextDecoder(best.label).decode(bytes), encoding: best.label };
}

export function detectLanguage(text) {
  const sample = text.slice(0, 200_000);
  let han = 0;
  let kana = 0;
  let hangul = 0;
  for (const ch of sample) {
    if (isHan(ch)) han++;
    else if (ch >= '぀' && ch <= 'ヿ') kana++;
    else if (ch >= '가' && ch <= '힣') hangul++;
  }
  const total = Math.max(sample.length, 1);
  if (hangul / total > 0.05) return 'ko';
  if (kana / total > 0.02) return 'ja';
  if (han / total > 0.05) {
    let trad = 0;
    let simp = 0;
    for (const ch of sample) {
      if (TRAD_ONLY.has(ch)) trad++;
      else if (SIMP_ONLY.has(ch)) simp++;
    }
    return trad > simp * 1.2 ? 'zh-Hant' : 'zh-Hans';
  }
  return 'en';
}

export function normalise(text) {
  let out = text.replace(/\r\n?/g, '\n').replace(/﻿/g, '');
  // Strip control characters the reader can't use, keeping \n and \t.
  out = out.replace(/[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]/g, '');
  out = out
    .split('\n')
    // Leading full-width spaces are the source file's indentation; the
    // stylesheet owns indentation in the EPUB.
    .map((line) => line.replace(/^[　\s]+/, '').replace(/\s+$/, ''))
    .join('\n');
  return out.replace(/\n{3,}/g, '\n\n').replace(/^\n+|\n+$/g, '');
}

export function load(bytes) {
  const { text: raw, encoding } = decode(bytes);
  const text = normalise(raw);
  return {
    text,
    lines: text.split('\n'),
    encoding,
    language: detectLanguage(text),
    charCount: text.length,
  };
}
