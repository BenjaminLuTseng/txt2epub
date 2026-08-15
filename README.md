# txt2epub

Drop a `.txt` book into a browser page, get a proper EPUB 3 back. Chinese,
Japanese and Korean text is a first-class case, not an afterthought.

Claude identifies the title and author, reviews the chapter split, and designs
the cover. Everything still works without an API key — you just get heuristics
and a locally generated cover instead.

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...     # optional; see "Without a key" below
python app.py                           # → http://127.0.0.1:5001
```

Port 5000 is occupied by AirPlay Receiver on macOS, so the default is 5001.
Override with `PORT=8080 python app.py`.

## What it does

| Step | How |
|---|---|
| **Encoding** | Decodes with every plausible codec and scores each result by how ordinary its characters are. Real Chinese draws heavily on a few hundred common characters; a wrong CJK codec produces rare-ideograph soup. Handles UTF-8/16/32, GB18030, Big5-HKSCS, Shift-JIS, EUC-KR, CP1252. |
| **Title & author** | Filename heuristics first (`《书名》作者.txt`, `Title - Author.txt`), then Claude reads the opening and closing pages and corrects them — stripping site watermarks and download banners, which is where filename parsing usually goes wrong. |
| **Chapters** | Runs every heading pattern it knows (`第一章`, `第一卷`, `Chapter 12`, `## Heading`, bare numbers) and keeps the family that explains the most lines, then folds in 序 / 楔子 / 后记 / Prologue / Epilogue. Claude reviews the result and can supply a better regex. You can also write your own. |
| **Paragraphs** | Tells Chinese-style "one line = one paragraph" apart from Gutenberg-style hard wrapping, by checking whether lines end on sentence-final punctuation and whether their lengths cluster at a wrap column. |
| **Cover** | Claude returns a structured design spec — palette, motif, layout, typeface, and the title broken into display lines. One renderer turns that spec into both a PNG and an SVG, so the two can't drift apart. |
| **EPUB** | Hand-written EPUB 3 with a `nav.xhtml` and an EPUB 2 `toc.ncx` fallback, a proper cover page, and a CJK stylesheet (Songti body text, 1.8 line-height, 2em paragraph indent, hanging punctuation). |

## Using it

1. Drop the file in. Encoding, language and chapter list appear immediately.
2. If a key is configured, Claude fills in title/author/description and checks
   the split automatically.
3. Fix anything that's wrong: edit chapter titles inline, untick chapters to
   drop them, click ◉ to read one, or re-split with your own regex.
4. **Generate cover** — optionally type a direction first ("quiet, botanical,
   1930s Shanghai"). Tweak the palette and motif afterwards, or download the
   SVG to edit elsewhere.
5. **Build EPUB**.

## Without a key

The app runs fine with no credentials — the header says so. You get filename
based title/author, heuristic chapter detection, and a cover from a small set
of built-in palettes. To enable the Claude features:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL` and `ant auth login` profiles are
honoured too — the SDK resolves them in that order. Set `TXT2EPUB_MODEL` to use
a different model (default `claude-opus-5`).

## Deploying to Fly.io

The container installs Noto CJK — **this is load-bearing.** macOS system fonts
(Songti, Hiragino) don't exist on Linux, and without a real CJK font every
Chinese title renders as tofu boxes. It's most of the image size.

```bash
brew install flyctl && fly auth login

fly launch --no-deploy            # pick a unique app name; keep the fly.toml
fly secrets set \
  ANTHROPIC_API_KEY="sk-ant-..." \
  APP_PASSWORD="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')" \
  SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
fly deploy
fly secrets list                  # names only; values are never shown again
```

`fly secrets set` stores values encrypted and injects them at runtime — they
never enter the repo or the image. Print the generated password once when you
create it, or set your own.

### Things worth knowing before it's public

- **Set `APP_PASSWORD`.** With it unset the app is open, and anyone who finds
  the URL spends your Anthropic credits. There is no rate limit beyond the
  password.
- **One machine, on purpose.** Uploaded books live in the worker's memory, so a
  second machine would not see a file uploaded to the first. `fly.toml` pins one
  machine and the Dockerfile runs one gunicorn worker with threads. If you scale
  out, move job state to Redis or a volume first.
- **Set `SECRET_KEY`.** Without it a random key is generated at boot and every
  deploy signs everyone out.
- **Uploads are capped at 32 MB** (`MAX_UPLOAD_MB`) and jobs expire after 6
  hours (`JOB_TTL_SECONDS`), keeping at most 12 (`MAX_JOBS`). Those defaults fit
  the 1 GB machine in `fly.toml`; raise the memory before raising the caps.
- Machines stop when idle and cold-start on the next request, which costs a few
  seconds. Set `min_machines_running = 1` to avoid that, at the cost of always-on
  billing.

Run the same image locally:

```bash
docker build -t txt2epub . && docker run --rm -p 8080:8080 \
  -e ANTHROPIC_API_KEY -e APP_PASSWORD=test txt2epub
```

## Layout

```
app.py                  Flask routes; jobs live in memory
txt2epub/ingest.py      encoding detection + normalisation
txt2epub/chapters.py    heading patterns, chapter and paragraph splitting
txt2epub/ai.py          Claude calls (structured JSON output)
txt2epub/cover.py       design spec → PNG and SVG
txt2epub/epubwriter.py  EPUB 3 packaging
```

## Notes

- Jobs are held in memory for the life of the process. Restarting the server
  means re-uploading; the page will tell you so rather than failing silently.
- Output has been checked for well-formed XML throughout and round-trips
  through pandoc, but has not been run against `epubcheck`. If you want that
  guarantee: `brew install epubcheck && epubcheck yourbook.epub`.
- Cover fonts come from macOS (Songti SC, Hiragino Sans GB, Georgia, Avenir
  Next). On another OS the renderer falls back to whatever it can find, so
  install a CJK font if covers come out as tofu.
