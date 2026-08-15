#!/usr/bin/env python3
"""Local web app: drop a .txt in, get an EPUB out.

    python app.py            # then open http://127.0.0.1:5000

Jobs live in memory for the life of the process — this is a single-user tool
running on localhost, not a service.
"""

from __future__ import annotations

import base64
import hmac
import io
import os
import re
import secrets
import threading
import time
import uuid
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

from txt2epub import ai, chapters as chap, cover as cover_mod, epubwriter, ingest

app = Flask(__name__)

# Books are held in memory, so the ceiling has to fit the machine. 200 MB was
# fine on a laptop; on a 512 MB Fly machine it is an out-of-memory crash.
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "32"))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# Set APP_PASSWORD to require a login. Unset (the default) leaves the app open,
# which is what you want on localhost and never what you want on the internet.
APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION_HOURS = int(os.environ.get("SESSION_HOURS", "12"))

# A generated key logs everyone out on restart; set SECRET_KEY to avoid that.
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("FLY_APP_NAME")),
    PERMANENT_SESSION_LIFETIME=SESSION_HOURS * 3600,
)

# Jobs expire so a long-lived server doesn't accumulate whole books in RAM.
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_SECONDS", str(6 * 3600)))
MAX_JOBS = int(os.environ.get("MAX_JOBS", "12"))

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _evict_locked() -> None:
    """Drop expired jobs, then oldest-first until under MAX_JOBS. Caller holds the lock."""
    now = time.time()
    for jid in [j for j, s in _jobs.items() if now - s["created"] > JOB_TTL_SECONDS]:
        _jobs.pop(jid, None)
    while len(_jobs) > MAX_JOBS:
        oldest = min(_jobs, key=lambda j: _jobs[j]["created"])
        _jobs.pop(oldest, None)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if APP_PASSWORD and not session.get("ok"):
            if request.path.startswith("/api/"):
                return jsonify(error="Session expired. Reload the page and sign in."), 401
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)

    return wrapper


@app.get("/login")
def login():
    if not APP_PASSWORD or session.get("ok"):
        return redirect(url_for("index"))
    return render_template("login.html", error=None)


@app.post("/login")
def do_login():
    supplied = request.form.get("password", "")
    # Constant-time compare so response timing can't be used to guess the value.
    if APP_PASSWORD and hmac.compare_digest(supplied, APP_PASSWORD):
        session.permanent = True
        session["ok"] = True
        target = request.args.get("next", "")
        # Only same-origin paths. "//evil.com" and "/\evil.com" also start with
        # "/" but browsers read them as protocol-relative URLs, so a bare
        # startswith("/") check is an open redirect.
        safe = target.startswith("/") and not target.startswith(("//", "/\\"))
        return redirect(target if safe else url_for("index"))
    time.sleep(1)  # blunt the rate of online guessing
    return render_template("login.html", error="Incorrect password."), 401


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/healthz")
def healthz():
    return {"ok": True, "jobs": len(_jobs)}


@app.errorhandler(413)
def too_large(_):
    return jsonify(error=f"That file is larger than the {MAX_UPLOAD_MB} MB limit."), 413

_FILENAME_NOISE = re.compile(
    r"(?:\[[^\]]*\]|【[^】]*】|\([^)]*\)|（[^）]*）|"
    r"全本|完本|全集|精校|校对|校對|txt|下载|下載|免费|免費|电子书|電子書)",
    re.IGNORECASE,
)


def guess_from_filename(name: str) -> tuple[str, str]:
    """Cheap title/author guess so the UI is populated even without the API."""
    stem = Path(name).stem
    stem = _FILENAME_NOISE.sub(" ", stem).strip(" -_·—")

    book = re.search(r"《([^》]+)》\s*(.*)", stem)
    if book:
        return book.group(1).strip(), book.group(2).strip(" -_by作者：: ")

    for sep in (" - ", " – ", " — ", "_by_", " by ", "－", "—", "-", "_"):
        if sep in stem:
            left, right = stem.split(sep, 1)
            left, right = left.strip(), right.strip()
            if left and right and len(right) <= 30:
                return left, right
    return stem.strip() or "Untitled", ""


def chapter_summary(chapters: list[chap.Chapter]) -> list[dict]:
    return [
        {
            "i": i,
            "title": c.title,
            "chars": c.char_count,
            "paras": len(c.paragraphs),
            "preview": c.preview,
            "include": c.include,
        }
        for i, c in enumerate(chapters)
    ]


def job(job_id: str) -> dict:
    with _lock:
        data = _jobs.get(job_id)
    if data is None:
        abort(404, "Unknown job — the server may have restarted. Re-upload the file.")
    return data


@app.get("/")
@login_required
def index():
    return render_template(
        "index.html", ai_ready=ai.available(), model=ai.MODEL, locked=bool(APP_PASSWORD)
    )


@app.post("/api/upload")
@login_required
def upload():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify(error="No file received."), 400

    raw = uploaded.read()
    if not raw.strip():
        return jsonify(error="That file is empty."), 400

    doc = ingest.load(raw)
    if not doc.text.strip():
        return jsonify(error="No readable text found in that file."), 400

    chapters, method = chap.split(doc.lines, language=doc.language)
    title, author = guess_from_filename(uploaded.filename)

    job_id = uuid.uuid4().hex
    with _lock:
        _jobs[job_id] = {
            "doc": doc,
            "raw": raw[:400_000],  # kept for /api/diagnose
            "filename": uploaded.filename,
            "chapters": chapters,
            "method": method,
            "created": time.time(),
            "cover_png": None,
            "cover_svg": None,
            "cover_spec": None,
        }
        _evict_locked()

    return jsonify(
        job_id=job_id,
        filename=uploaded.filename,
        encoding=doc.encoding,
        language=doc.language,
        chars=doc.char_count,
        method=method,
        title=title,
        author=author,
        description="",
        series="",
        ai_source="filename",
        chapters=chapter_summary(chapters),
    )


@app.get("/api/_jobs")
@login_required
def list_jobs():
    """Debug aid: which files are loaded right now."""
    with _lock:
        return jsonify(
            jobs=[
                {
                    "job_id": jid,
                    "filename": s["filename"],
                    "encoding": s["doc"].encoding,
                    "language": s["doc"].language,
                    "chars": s["doc"].char_count,
                    "method": s["method"],
                    "chapters": len(s["chapters"]),
                }
                for jid, s in _jobs.items()
            ]
        )


@app.get("/api/diagnose")
@login_required
def diagnose():
    """Everything needed to work out why a split went wrong."""
    state = job(request.args.get("job_id", ""))
    doc: ingest.Document = state["doc"]
    lines = doc.lines

    patterns = {
        name: len(chap._candidate_lines(lines, pattern)) for name, pattern in chap.PATTERNS
    }
    patterns["special"] = len(chap._candidate_lines(lines, chap.SPECIAL))
    idxs, _ = chap.find_headings(lines)
    heads = set(idxs)

    first_lines = []
    for i, line in enumerate(lines):
        if line.strip():
            first_lines.append({"n": i, "heading": i in heads, "text": line[:100]})
            if len(first_lines) >= 40:
                break

    # Lines that look heading-ish but matched nothing — the usual culprits.
    near_misses = [
        {"n": i, "text": line[:100]}
        for i, line in enumerate(lines)
        if line.strip()
        and i not in heads
        and len(line.strip()) <= 40
        and ("章" in line or "回" in line or "卷" in line or "Chapter" in line)
    ][:25]

    return jsonify(
        filename=state["filename"],
        encoding=doc.encoding,
        language=doc.language,
        chars=doc.char_count,
        lines=len(lines),
        blank_lines=sum(1 for line in lines if not line.strip()),
        longest_line=max((len(line) for line in lines), default=0),
        method=state["method"],
        chapter_count=len(state["chapters"]),
        pattern_hits=patterns,
        encoding_candidates=[
            {"encoding": e, "score": s, "sample": t}
            for e, s, t in ingest.score_candidates(state.get("raw") or b"")
        ],
        first_lines=first_lines,
        near_misses=near_misses,
        chapters=[
            {"title": c.title[:60], "chars": c.char_count} for c in state["chapters"][:15]
        ],
    )


@app.post("/api/analyze")
@login_required
def analyze():
    """Ask Claude for the real title/author and to sanity-check the split."""
    data = request.get_json(force=True)
    state = job(data["job_id"])
    doc: ingest.Document = state["doc"]
    result: dict = {"warnings": []}

    headings = [c.title for c in state["chapters"]]

    try:
        meta = ai.detect_metadata(
            filename=state["filename"],
            head=doc.text[:4000],
            tail=doc.text[-1500:],
            headings=headings,
        )
        result["metadata"] = meta
    except ai.AIUnavailable as exc:
        return jsonify(error=str(exc)), 503

    body_lines = [
        line for line in doc.lines if line.strip() and line.strip() not in set(headings)
    ]
    sample = body_lines[:: max(1, len(body_lines) // 25)][:25]
    try:
        review = ai.review_chapters(
            method=state["method"],
            headings=headings,
            sample_lines=sample,
            total=len(state["chapters"]),
        )
    except ai.AIUnavailable as exc:
        review = {"verdict": "good", "regex": "", "note": f"Split not reviewed ({exc})."}

    result["review"] = review

    if review.get("verdict") == "use_regex" and review.get("regex"):
        try:
            re.compile(review["regex"])
            new_chapters, _ = chap.split(doc.lines, review["regex"], language=doc.language)
        except re.error as exc:
            result["warnings"].append(f"Claude suggested an invalid regex ({exc}); kept the original split.")
            new_chapters = []
        if 1 < len(new_chapters) <= 5000:
            with _lock:
                state["chapters"] = new_chapters
                state["method"] = "claude-regex"
            result["chapters"] = chapter_summary(new_chapters)
        elif new_chapters:
            result["warnings"].append(
                f"Claude's regex produced {len(new_chapters)} chapters; kept the original split."
            )

    return jsonify(result)


@app.post("/api/resplit")
@login_required
def resplit():
    data = request.get_json(force=True)
    state = job(data["job_id"])
    doc: ingest.Document = state["doc"]
    pattern = (data.get("regex") or "").strip()

    if data.get("mode") == "single":
        paragraphs = chap.to_paragraphs(doc.lines)
        chapters = [chap.Chapter(title=data.get("title") or "Full Text", paragraphs=paragraphs)]
        method = "single"
    else:
        try:
            chapters, method = chap.split(doc.lines, pattern or None, language=doc.language)
        except re.error as exc:
            return jsonify(error=f"Invalid regular expression: {exc}"), 400

    if not chapters:
        return jsonify(error="That pattern matched nothing usable."), 400

    with _lock:
        state["chapters"] = chapters
        state["method"] = method
    return jsonify(method=method, chapters=chapter_summary(chapters))


@app.post("/api/cover")
@login_required
def make_cover():
    data = request.get_json(force=True)
    state = job(data["job_id"])
    doc: ingest.Document = state["doc"]
    title = (data.get("title") or "Untitled").strip()
    author = (data.get("author") or "").strip()
    use_ai = bool(data.get("use_ai", True))

    note = ""
    spec = None
    if use_ai:
        try:
            spec = ai.design_cover(
                title=title,
                author=author,
                language=doc.language,
                description=data.get("description") or "",
                opening=doc.text[:1500],
                style_hint=data.get("style_hint") or "",
            )
        except ai.AIUnavailable as exc:
            note = f"Designed locally — {exc}"
    if spec is None:
        spec = cover_mod.fallback_spec(title, author, seed=data.get("style_hint") or title)
        note = note or "Designed locally (no API call)."

    # The user's edits to title/author always win over the model's copy of them.
    spec["author_line"] = author
    if not spec.get("title_lines"):
        spec["title_lines"] = [title]

    png = cover_mod.render_png(spec)
    svg = cover_mod.render_svg(spec)
    with _lock:
        state["cover_png"] = png
        state["cover_svg"] = svg
        state["cover_spec"] = spec

    return jsonify(
        png="data:image/png;base64," + base64.b64encode(png).decode(),
        spec=spec,
        note=note,
    )


@app.post("/api/cover/spec")
@login_required
def cover_from_spec():
    """Re-render after the user hand-edits the palette or motif."""
    data = request.get_json(force=True)
    state = job(data["job_id"])
    spec = data.get("spec") or {}
    try:
        png = cover_mod.render_png(spec)
        svg = cover_mod.render_svg(spec)
    except Exception as exc:  # noqa: BLE001
        return jsonify(error=f"Could not render that spec: {exc}"), 400
    with _lock:
        state["cover_png"] = png
        state["cover_svg"] = svg
        state["cover_spec"] = spec
    return jsonify(png="data:image/png;base64," + base64.b64encode(png).decode(), spec=spec)


@app.get("/api/cover.svg")
@login_required
def cover_svg():
    state = job(request.args.get("job_id", ""))
    if not state["cover_svg"]:
        abort(404, "No cover has been generated yet.")
    return send_file(
        io.BytesIO(state["cover_svg"].encode()),
        mimetype="image/svg+xml",
        as_attachment=True,
        download_name="cover.svg",
    )


@app.get("/api/chapter")
@login_required
def chapter_text():
    state = job(request.args.get("job_id", ""))
    index = int(request.args.get("i", 0))
    chapters = state["chapters"]
    if not 0 <= index < len(chapters):
        abort(404)
    c = chapters[index]
    return jsonify(title=c.title, paragraphs=c.paragraphs[:60], total=len(c.paragraphs))


@app.post("/api/build")
@login_required
def build():
    data = request.get_json(force=True)
    state = job(data["job_id"])
    doc: ingest.Document = state["doc"]
    chapters = state["chapters"]

    edits = {int(e["i"]): e for e in data.get("chapters", [])}
    selected = []
    for i, c in enumerate(chapters):
        edit = edits.get(i)
        if edit is not None and not edit.get("include", True):
            continue
        title = (edit or {}).get("title") or c.title
        selected.append(chap.Chapter(title=title.strip() or c.title, paragraphs=c.paragraphs))

    if not selected:
        return jsonify(error="Every chapter is excluded — nothing to build."), 400

    title = (data.get("title") or "Untitled").strip()
    epub_bytes = epubwriter.build(
        title=title,
        author=(data.get("author") or "").strip(),
        language=data.get("language") or doc.language,
        chapters=selected,
        cover_png=state["cover_png"] if data.get("include_cover", True) else None,
        description=(data.get("description") or "").strip(),
        series=(data.get("series") or "").strip(),
    )

    safe = re.sub(r'[\\/:*?"<>|]+', "_", title).strip() or "book"
    return send_file(
        io.BytesIO(epub_bytes),
        mimetype="application/epub+zip",
        as_attachment=True,
        download_name=f"{safe}.epub",
    )


if __name__ == "__main__":
    import os

    # 5000 is taken by AirPlay Receiver on macOS, so default to 5001.
    port = int(os.environ.get("PORT", "5001"))
    print(f"\n  txt2epub  →  http://127.0.0.1:{port}")
    print(f"  Claude: {'ready (' + ai.MODEL + ')' if ai.available() else 'not configured — heuristics only'}\n")
    app.run(host="127.0.0.1", port=port, debug=False)
