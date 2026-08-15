// Claude calls straight from the browser.
//
// The API only sets CORS headers when `anthropic-dangerous-direct-browser-access`
// is present — verified against a preflight: without it the response carries no
// access-control-allow-origin and the browser blocks the call.
//
// The key lives in this browser's localStorage and is sent only to
// api.anthropic.com. It is never transmitted to GitHub Pages, which serves
// static files and has no backend to receive it.

const ENDPOINT = 'https://api.anthropic.com/v1/messages';
export const MODEL = 'claude-opus-5';
const KEY_STORAGE = 'txt2epub.apiKey';

export const getKey = () => localStorage.getItem(KEY_STORAGE) || '';
export const setKey = (k) => (k ? localStorage.setItem(KEY_STORAGE, k) : localStorage.removeItem(KEY_STORAGE));
export const available = () => Boolean(getKey());

export class AIUnavailable extends Error {}

async function ask(prompt, schema, effort, maxTokens = 8000) {
  const key = getKey();
  if (!key) throw new AIUnavailable('No API key set.');

  let response;
  try {
    response = await fetch(ENDPOINT, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': key,
        'anthropic-version': '2023-06-01',
        'anthropic-dangerous-direct-browser-access': 'true',
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: maxTokens,
        output_config: { effort, format: { type: 'json_schema', schema } },
        messages: [{ role: 'user', content: prompt }],
      }),
    });
  } catch (err) {
    throw new AIUnavailable(`Network error reaching the API: ${err.message}`);
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = body?.error?.message || detail;
    } catch { /* non-JSON error body */ }
    if (response.status === 401) detail = 'API key rejected. Check the key and try again.';
    throw new AIUnavailable(detail);
  }

  const data = await response.json();
  if (data.stop_reason === 'refusal') throw new AIUnavailable('The model declined this request.');
  const text = (data.content || []).find((b) => b.type === 'text')?.text;
  if (!text) throw new AIUnavailable('Empty response from the model.');
  return JSON.parse(text);
}

// -------------------------------------------------------------- book metadata

const METADATA_SCHEMA = {
  type: 'object',
  properties: {
    title: { type: 'string', description: 'Book title in its original language.' },
    author: { type: 'string', description: 'Author name, or empty string if genuinely unknown.' },
    series: { type: 'string', description: 'Series name and number, or empty string.' },
    language: { type: 'string', enum: ['zh-Hans', 'zh-Hant', 'en', 'ja', 'ko', 'other'] },
    description: { type: 'string', description: 'One or two sentences describing the book.' },
    confidence: { type: 'string', enum: ['high', 'medium', 'low'] },
    notes: { type: 'string', description: 'Where the title and author were found, briefly.' },
  },
  required: ['title', 'author', 'series', 'language', 'description', 'confidence', 'notes'],
  additionalProperties: false,
};

export function detectMetadata({ filename, head, tail, headings }) {
  return ask(
    `Identify the title and author of this book from a plain-text file.

Filename: ${filename}

First part of the file:
<head>
${head}
</head>

End of the file:
<tail>
${tail}
</tail>

First chapter headings found:
${headings.slice(0, 15).join('\n') || '(none detected)'}

The filename often carries the title and author (patterns like
"《书名》作者.txt", "书名 - 作者.txt", "Title - Author.txt"), but the text itself
takes precedence when the two disagree. Strip site watermarks, download-source
banners, and forum signatures — those are not the author. If the author is
genuinely not recoverable, return an empty string rather than a guess.`,
    METADATA_SCHEMA,
    'low',
    4000,
  );
}

// ------------------------------------------------------------ chapter review

const CHAPTERS_SCHEMA = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['good', 'use_regex', 'no_chapters'] },
    regex: {
      type: 'string',
      description: 'JavaScript regex matching a full heading line, anchored with ^ and $. Empty unless verdict is use_regex.',
    },
    note: { type: 'string', description: 'One short sentence for the user.' },
  },
  required: ['verdict', 'regex', 'note'],
  additionalProperties: false,
};

export function reviewChapters({ method, headings, sampleLines, total }) {
  return ask(
    `Review an automatic chapter split of a plain-text book.

Detection method that won: ${method}
Chapters found: ${total}

Detected headings (first 40):
${headings.slice(0, 40).join('\n')}

Random sample of lines that were NOT treated as headings:
${sampleLines.slice(0, 25).join('\n')}

Decide one of:
- "good": the headings are real chapter headings and nothing obvious was missed.
- "use_regex": the split is wrong or incomplete. Supply a JavaScript regex that
  matches an entire heading line (anchored ^...$, tested against each trimmed
  line). Do not include flags or delimiters.
- "no_chapters": this file has no chapter structure and should stay as one piece.

Prefer "good" unless there is a concrete problem: headings that are actually
body text, an obvious numbering gap, or a heading style visible in the sample
lines that the detector clearly missed.`,
    CHAPTERS_SCHEMA,
    'low',
    4000,
  );
}

// -------------------------------------------------------------- cover design

const COVER_SCHEMA = {
  type: 'object',
  properties: {
    palette: {
      type: 'object',
      properties: {
        background: { type: 'string', description: 'Hex colour, e.g. #12202e' },
        background2: { type: 'string', description: 'Second gradient stop, hex.' },
        accent: { type: 'string', description: 'Motif and rule colour, hex.' },
        title: { type: 'string', description: 'Title text colour, hex.' },
        author: { type: 'string', description: 'Author text colour, hex.' },
      },
      required: ['background', 'background2', 'accent', 'title', 'author'],
      additionalProperties: false,
    },
    layout: { type: 'string', enum: ['centered', 'upper', 'lower', 'band'] },
    motif: {
      type: 'string',
      enum: ['none', 'arcs', 'rings', 'mountains', 'grid', 'rays', 'waves', 'bars', 'dots', 'frame', 'horizon'],
    },
    motif_intensity: { type: 'string', enum: ['subtle', 'medium', 'bold'] },
    typeface: { type: 'string', enum: ['serif', 'sans'] },
    title_lines: {
      type: 'array',
      items: { type: 'string' },
      description: 'The title broken into 1-3 display lines at sensible word or phrase boundaries.',
    },
    subtitle: { type: 'string', description: 'Short subtitle or series line; empty string if none.' },
    author_line: { type: 'string', description: 'Author as it should appear on the cover; empty if unknown.' },
    rationale: { type: 'string', description: 'One sentence on why this design suits the book.' },
  },
  required: ['palette', 'layout', 'motif', 'motif_intensity', 'typeface', 'title_lines', 'subtitle', 'author_line', 'rationale'],
  additionalProperties: false,
};

export function designCover({ title, author, language, description, opening, styleHint = '' }) {
  const hint = styleHint.trim() ? `\nThe user asked for this direction: ${styleHint}\n` : '';
  return ask(
    `Design a book cover as a structured spec. A renderer turns your spec into
a 1600x2400 cover, so every choice below is the actual design.

Title: ${title}
Author: ${author || '(unknown)'}
Language: ${language}
What the book is about: ${description || '(unknown)'}

Opening lines of the book:
<opening>
${opening.slice(0, 1200)}
</opening>
${hint}
Design guidance:
- Choose colours that suit this specific book's subject and mood. Avoid the
  default AI palette of purple-to-blue gradients on dark navy, and avoid
  generic stock-cover teal.
- The two background colours form a vertical gradient; keep them close in hue
  so the field reads as one surface, and keep strong contrast against the title
  colour so the type stays legible.
- Break \`title_lines\` at real phrase boundaries. For Chinese, break by meaning
  rather than character count, and never leave a single trailing character
  alone on a line. One line is best for short titles; never exceed three.
- Pick the motif that carries meaning for this book, not decoration for its own
  sake. "none" is a legitimate and often strong choice for literary fiction.
- Use serif for literary, historical, and classical work; sans for modern,
  technical, or contemporary work.`,
    COVER_SCHEMA,
    'medium',
    8000,
  );
}
