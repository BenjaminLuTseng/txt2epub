"""Render a cover from a design spec, to PNG and to SVG.

Both backends draw the same list of primitives computed once, so the SVG you
download and the PNG embedded in the EPUB are the same picture. This is why the
model returns a structured spec rather than raw SVG: no rasteriser is needed,
and the two outputs cannot drift apart.
"""

from __future__ import annotations

import glob
import io
import math
import os
import random
from typing import Any

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1600, 2400
MARGIN = 150

# macOS paths first (local use), then the Linux/container paths the Docker
# image installs. Without a real CJK font every Chinese title renders as tofu
# boxes, so the CJK entries also fall back to a filesystem scan below.
_FONT_FILES: dict[tuple[str, bool], list[str]] = {
    ("serif", True): [
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSerifCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSerifCJK-Regular.ttc",
    ],
    ("sans", True): [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ],
    ("serif", False): [
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    ],
    ("sans", False): [
        "/System/Library/Fonts/Avenir Next.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ],
}

_CJK_GLOBS = (
    "/usr/share/fonts/**/*CJK*.ttc",
    "/usr/share/fonts/**/*CJK*.otf",
    "/usr/share/fonts/**/*CJK*.ttf",
    "/usr/share/fonts/**/*Han*.otf",
    "/usr/share/fonts/**/*Song*.tt*",
    "/usr/share/fonts/**/*Hei*.tt*",
)

_FONT_STACKS = {
    ("serif", True): "Songti SC, STSong, Noto Serif CJK SC, Georgia, serif",
    ("sans", True): "PingFang SC, Hiragino Sans GB, Noto Sans CJK SC, sans-serif",
    ("serif", False): "Georgia, Times New Roman, serif",
    ("sans", False): "Avenir Next, Helvetica Neue, Arial, sans-serif",
}

_FALLBACKS = [
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def has_cjk(text: str) -> bool:
    return any("一" <= c <= "鿿" or "぀" <= c <= "ヿ" or "가" <= c <= "힣" for c in text)


def _font_path(typeface: str, cjk: bool) -> str:
    for candidate in _FONT_FILES[(typeface, cjk)]:
        if os.path.exists(candidate):
            return candidate
    if cjk:
        # Distributions disagree on where Noto CJK lands; find it rather than
        # silently falling through to a Latin font and rendering tofu.
        for pattern in _CJK_GLOBS:
            hits = sorted(glob.glob(pattern, recursive=True))
            if hits:
                return hits[0]
    for candidate in _FALLBACKS:
        if os.path.exists(candidate):
            return candidate
    raise RuntimeError(
        "No usable font found. On Linux install fonts-noto-cjk "
        "(Debian/Ubuntu) or google-noto-sans-cjk-fonts (Fedora)."
    )


def _bold_sibling(path: str) -> str | None:
    """Noto ships weights as separate files: ...-Regular.ttc / ...-Bold.ttc."""
    for old, new in (("-Regular", "-Bold"), ("Regular", "Bold"), (".ttf", " Bold.ttf")):
        if old in path:
            candidate = path.replace(old, new)
            if candidate != path and os.path.exists(candidate):
                return candidate
    return None


def _load(path: str, size: int, bold: bool) -> ImageFont.FreeTypeFont:
    """Load a font, picking the bold face out of a .ttc collection when asked."""
    if bold:
        sibling = _bold_sibling(path)
        if sibling:
            path = sibling

    if not path.endswith(".ttc"):
        return ImageFont.truetype(path, size)

    best = None
    for index in range(24):
        try:
            font = ImageFont.truetype(path, size, index=index)
        except Exception:  # noqa: BLE001 - ran past the end of the collection
            break
        family, style = font.getname()
        style = (style or "").lower()
        if "italic" in style or "oblique" in style:
            continue
        # Noto CJK collections bundle SC/TC/JP/KR; prefer the SC face so a
        # simplified title doesn't pick up Japanese glyph variants.
        preferred_family = "sc" in (family or "").lower() or "cjk" not in (family or "").lower()
        if bold and ("bold" in style or "demi" in style):
            if preferred_family:
                return font
            best = best or font
        elif not bold and style in ("regular", "book", "light", "medium"):
            if preferred_family:
                return font
            best = best or font
    return best or ImageFont.truetype(path, size, index=0)


def _hex(value: str, default: str) -> tuple[int, int, int]:
    value = (value or "").strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        value = default.lstrip("#")
    try:
        return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        d = default.lstrip("#")
        return tuple(int(d[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _rgb_str(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


# --------------------------------------------------------------------------
# Motif geometry — computed once, rendered by both backends
# --------------------------------------------------------------------------


def _motif_primitives(motif: str, seed: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    p: list[dict[str, Any]] = []
    cx = WIDTH / 2

    if motif == "arcs":
        cy = HEIGHT * 0.30
        for i in range(7):
            r = 260 + i * 145
            pts = [
                (cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a)))
                for a in range(20, 161, 4)
            ]
            p.append({"t": "polyline", "pts": pts, "w": 5})
    elif motif == "rings":
        cy = HEIGHT * 0.42
        for i in range(6):
            p.append({"t": "circle", "c": (cx, cy), "r": 190 + i * 150, "w": 4})
    elif motif == "mountains":
        base = HEIGHT * 0.80
        for layer, (height, step) in enumerate(((470, 400), (330, 300), (200, 230))):
            pts = [(-60.0, base)]
            x = -60.0
            up = True
            while x < WIDTH + 60:
                x += step
                pts.append((x, base - height * (0.55 + 0.45 * rng.random()) if up else base - height * 0.15))
                up = not up
            pts.append((WIDTH + 60.0, base))
            p.append({"t": "polygon", "pts": pts, "layer": layer})
    elif motif == "grid":
        for x in range(MARGIN, WIDTH - MARGIN + 1, 110):
            p.append({"t": "line", "pts": [(x, MARGIN), (x, HEIGHT - MARGIN)], "w": 2})
        for y in range(MARGIN, HEIGHT - MARGIN + 1, 110):
            p.append({"t": "line", "pts": [(MARGIN, y), (WIDTH - MARGIN, y)], "w": 2})
    elif motif == "rays":
        oy = HEIGHT * 1.05
        for i in range(17):
            angle = math.radians(-90 + (i - 8) * 6.4)
            p.append(
                {
                    "t": "line",
                    "pts": [(cx, oy), (cx + 3000 * math.cos(angle), oy + 3000 * math.sin(angle))],
                    "w": 6,
                }
            )
    elif motif == "waves":
        for i in range(9):
            y0 = HEIGHT * 0.58 + i * 105
            pts = [(x, y0 + 42 * math.sin(x / 210.0 + i * 0.6)) for x in range(-40, WIDTH + 41, 20)]
            p.append({"t": "polyline", "pts": pts, "w": 5})
    elif motif == "bars":
        base = HEIGHT - MARGIN
        x = MARGIN
        while x < WIDTH - MARGIN:
            w = 44
            h = 120 + rng.random() * 620
            p.append({"t": "rectf", "xy": (x, base - h, x + w, base)})
            x += w + 34
    elif motif == "dots":
        for y in range(int(HEIGHT * 0.10), int(HEIGHT * 0.92), 96):
            for x in range(MARGIN, WIDTH - MARGIN + 1, 96):
                p.append({"t": "circle", "c": (x, y), "r": 7, "w": 0})
    elif motif == "frame":
        p.append({"t": "rect", "xy": (90, 90, WIDTH - 90, HEIGHT - 90), "w": 6})
        p.append({"t": "rect", "xy": (118, 118, WIDTH - 118, HEIGHT - 118), "w": 2})
    elif motif == "horizon":
        y = HEIGHT * 0.72
        p.append({"t": "circle", "c": (cx, y - 210), "r": 240, "w": 6})
        p.append({"t": "line", "pts": [(MARGIN, y), (WIDTH - MARGIN, y)], "w": 6})
        p.append({"t": "line", "pts": [(MARGIN, y + 46), (WIDTH - MARGIN, y + 46)], "w": 2})
    return p


_ALPHA = {"subtle": 46, "medium": 92, "bold": 150}


# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------


def _fit_font(lines: list[str], path: str, max_w: int, start: int, minimum: int) -> ImageFont.FreeTypeFont:
    probe = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(probe)
    size = start
    while size > minimum:
        font = _load(path, size, bold=True)
        widest = max((draw.textlength(line, font=font) for line in lines), default=0)
        if widest <= max_w:
            return font
        size -= 6
    return _load(path, minimum, bold=True)


def _layout(spec: dict, title_font_size_hint: int | None = None) -> dict:
    """Compute every text position once; both backends consume this."""
    title_lines = [str(x) for x in (spec.get("title_lines") or []) if str(x).strip()][:3]
    if not title_lines:
        title_lines = ["Untitled"]
    subtitle = str(spec.get("subtitle") or "").strip()
    author = str(spec.get("author_line") or "").strip()
    typeface = spec.get("typeface") if spec.get("typeface") in ("serif", "sans") else "serif"
    cjk = has_cjk(" ".join(title_lines) + subtitle + author)
    path = _font_path(typeface, cjk)

    max_w = WIDTH - 2 * MARGIN
    start = 250 if len(title_lines) == 1 else 200
    title_font = _fit_font(title_lines, path, max_w, start, 64)
    size = title_font.size
    line_gap = int(size * (1.30 if cjk else 1.18))
    block_h = line_gap * len(title_lines)

    layout = spec.get("layout") if spec.get("layout") in ("centered", "upper", "lower", "band") else "centered"
    anchor = {"centered": 0.40, "upper": 0.22, "lower": 0.60, "band": 0.44}[layout]
    top = HEIGHT * anchor - block_h / 2

    baselines = []
    ascent = title_font.getmetrics()[0]
    for i in range(len(title_lines)):
        baselines.append(top + i * line_gap + ascent + (line_gap - size) * 0.25)

    sub_size = max(40, int(size * 0.26))
    author_size = max(46, int(size * 0.30))

    y_sub = baselines[-1] + line_gap * 0.72 if subtitle else None
    rule_y = (y_sub if y_sub else baselines[-1]) + line_gap * 0.55
    y_author = HEIGHT * 0.885

    return {
        "title_lines": title_lines,
        "subtitle": subtitle,
        "author": author,
        "font_path": path,
        "typeface": typeface,
        "cjk": cjk,
        "title_size": size,
        "sub_size": sub_size,
        "author_size": author_size,
        "baselines": baselines,
        "y_sub": y_sub,
        "rule_y": rule_y,
        "y_author": y_author,
        "band": layout == "band",
        "band_box": (0, top - line_gap * 0.45, WIDTH, rule_y + line_gap * 0.25),
    }


# --------------------------------------------------------------------------
# PNG backend
# --------------------------------------------------------------------------


def render_png(spec: dict) -> bytes:
    pal = spec.get("palette") or {}
    bg1 = _hex(pal.get("background", ""), "#1d2b36")
    bg2 = _hex(pal.get("background2", ""), "#0f1a22")
    accent = _hex(pal.get("accent", ""), "#c8a06a")
    title_col = _hex(pal.get("title", ""), "#f6f2e8")
    author_col = _hex(pal.get("author", ""), "#d8cfbe")

    image = Image.new("RGB", (WIDTH, HEIGHT), bg1)
    draw = ImageDraw.Draw(image)
    for y in range(HEIGHT):
        t = y / (HEIGHT - 1)
        draw.line(
            [(0, y), (WIDTH, y)],
            fill=tuple(round(bg1[i] + (bg2[i] - bg1[i]) * t) for i in range(3)),
        )

    motif = spec.get("motif") or "none"
    if motif != "none":
        alpha = _ALPHA.get(spec.get("motif_intensity") or "medium", 92)
        overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        for prim in _motif_primitives(motif, seed=hash(str(spec.get("title_lines"))) & 0xFFFF):
            a = alpha
            if prim.get("layer") is not None:
                a = int(alpha * (0.45 + 0.28 * prim["layer"]))
            colour = (*accent, min(255, a))
            kind = prim["t"]
            if kind == "line":
                od.line(prim["pts"], fill=colour, width=prim["w"])
            elif kind == "polyline":
                od.line(prim["pts"], fill=colour, width=prim["w"], joint="curve")
            elif kind == "polygon":
                od.polygon(prim["pts"], fill=colour)
            elif kind == "circle":
                cx, cy = prim["c"]
                r = prim["r"]
                box = (cx - r, cy - r, cx + r, cy + r)
                if prim["w"] == 0:
                    od.ellipse(box, fill=colour)
                else:
                    od.ellipse(box, outline=colour, width=prim["w"])
            elif kind == "rect":
                od.rectangle(prim["xy"], outline=colour, width=prim["w"])
            elif kind == "rectf":
                od.rectangle(prim["xy"], fill=colour)
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)

    lay = _layout(spec)

    if lay["band"]:
        x0, y0, x1, y1 = lay["band_box"]
        band = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        ImageDraw.Draw(band).rectangle((x0, y0, x1, y1), fill=(*bg2, 225))
        image = Image.alpha_composite(image.convert("RGBA"), band).convert("RGB")
        draw = ImageDraw.Draw(image)

    title_font = _load(lay["font_path"], lay["title_size"], bold=True)
    for line, baseline in zip(lay["title_lines"], lay["baselines"]):
        w = draw.textlength(line, font=title_font)
        draw.text(((WIDTH - w) / 2, baseline), line, font=title_font, fill=title_col, anchor="ls")

    if lay["subtitle"]:
        sub_font = _load(lay["font_path"], lay["sub_size"], bold=False)
        w = draw.textlength(lay["subtitle"], font=sub_font)
        draw.text(((WIDTH - w) / 2, lay["y_sub"]), lay["subtitle"], font=sub_font, fill=author_col, anchor="ls")

    rule_w = min(360, WIDTH - 2 * MARGIN)
    draw.line(
        [((WIDTH - rule_w) / 2, lay["rule_y"]), ((WIDTH + rule_w) / 2, lay["rule_y"])],
        fill=accent,
        width=5,
    )

    if lay["author"]:
        author_font = _load(lay["font_path"], lay["author_size"], bold=False)
        w = draw.textlength(lay["author"], font=author_font)
        draw.text(((WIDTH - w) / 2, lay["y_author"]), lay["author"], font=author_font, fill=author_col, anchor="ls")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


# --------------------------------------------------------------------------
# SVG backend
# --------------------------------------------------------------------------


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def render_svg(spec: dict) -> str:
    pal = spec.get("palette") or {}
    bg1 = _rgb_str(_hex(pal.get("background", ""), "#1d2b36"))
    bg2 = _rgb_str(_hex(pal.get("background2", ""), "#0f1a22"))
    accent = _rgb_str(_hex(pal.get("accent", ""), "#c8a06a"))
    title_col = _rgb_str(_hex(pal.get("title", ""), "#f6f2e8"))
    author_col = _rgb_str(_hex(pal.get("author", ""), "#d8cfbe"))

    lay = _layout(spec)
    stack = _FONT_STACKS[(lay["typeface"], lay["cjk"])]
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" '
        f'viewBox="0 0 {WIDTH} {HEIGHT}">',
        "<defs><linearGradient id='bg' x1='0' y1='0' x2='0' y2='1'>"
        f"<stop offset='0' stop-color='{bg1}'/><stop offset='1' stop-color='{bg2}'/>"
        "</linearGradient></defs>",
        f"<rect width='{WIDTH}' height='{HEIGHT}' fill='url(#bg)'/>",
    ]

    motif = spec.get("motif") or "none"
    if motif != "none":
        alpha = _ALPHA.get(spec.get("motif_intensity") or "medium", 92) / 255.0
        parts.append(f"<g stroke='{accent}' fill='none' stroke-linecap='round' stroke-linejoin='round'>")
        for prim in _motif_primitives(motif, seed=hash(str(spec.get("title_lines"))) & 0xFFFF):
            a = alpha
            if prim.get("layer") is not None:
                a = alpha * (0.45 + 0.28 * prim["layer"])
            a = round(min(1.0, a), 3)
            kind = prim["t"]
            if kind in ("line", "polyline"):
                pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in prim["pts"])
                parts.append(f"<polyline points='{pts}' stroke-width='{prim['w']}' stroke-opacity='{a}'/>")
            elif kind == "polygon":
                pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in prim["pts"])
                parts.append(f"<polygon points='{pts}' fill='{accent}' fill-opacity='{a}' stroke='none'/>")
            elif kind == "circle":
                cx, cy = prim["c"]
                if prim["w"] == 0:
                    parts.append(
                        f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{prim['r']}' fill='{accent}' "
                        f"fill-opacity='{a}' stroke='none'/>"
                    )
                else:
                    parts.append(
                        f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{prim['r']}' "
                        f"stroke-width='{prim['w']}' stroke-opacity='{a}'/>"
                    )
            elif kind in ("rect", "rectf"):
                x0, y0, x1, y1 = prim["xy"]
                common = f"x='{x0:.1f}' y='{y0:.1f}' width='{x1 - x0:.1f}' height='{y1 - y0:.1f}'"
                if kind == "rectf":
                    parts.append(f"<rect {common} fill='{accent}' fill-opacity='{a}' stroke='none'/>")
                else:
                    parts.append(f"<rect {common} stroke-width='{prim['w']}' stroke-opacity='{a}'/>")
        parts.append("</g>")

    if lay["band"]:
        x0, y0, x1, y1 = lay["band_box"]
        parts.append(
            f"<rect x='{x0}' y='{y0:.1f}' width='{x1 - x0}' height='{y1 - y0:.1f}' "
            f"fill='{bg2}' fill-opacity='0.88'/>"
        )

    for line, baseline in zip(lay["title_lines"], lay["baselines"]):
        parts.append(
            f"<text x='{WIDTH / 2}' y='{baseline:.1f}' text-anchor='middle' fill='{title_col}' "
            f"font-family='{stack}' font-size='{lay['title_size']}' font-weight='700'>{_esc(line)}</text>"
        )

    if lay["subtitle"]:
        parts.append(
            f"<text x='{WIDTH / 2}' y='{lay['y_sub']:.1f}' text-anchor='middle' fill='{author_col}' "
            f"font-family='{stack}' font-size='{lay['sub_size']}'>{_esc(lay['subtitle'])}</text>"
        )

    rule_w = min(360, WIDTH - 2 * MARGIN)
    parts.append(
        f"<line x1='{(WIDTH - rule_w) / 2}' y1='{lay['rule_y']:.1f}' x2='{(WIDTH + rule_w) / 2}' "
        f"y2='{lay['rule_y']:.1f}' stroke='{accent}' stroke-width='5'/>"
    )

    if lay["author"]:
        parts.append(
            f"<text x='{WIDTH / 2}' y='{lay['y_author']:.1f}' text-anchor='middle' fill='{author_col}' "
            f"font-family='{stack}' font-size='{lay['author_size']}'>{_esc(lay['author'])}</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts)


def fallback_spec(title: str, author: str, seed: str = "") -> dict:
    """A deterministic, decent-looking spec for when the API is unavailable."""
    schemes = [
        ("#22303c", "#131d26", "#c9a227", "#f5f1e6", "#cfc6b2", "arcs"),
        ("#2d2320", "#171110", "#c4703f", "#f6ece0", "#d8c3b0", "horizon"),
        ("#1c2b28", "#0d1614", "#7fae9b", "#f0f5f1", "#c3d3cb", "waves"),
        ("#2b2438", "#161122", "#b08bc4", "#f2edf7", "#cfc2dc", "rings"),
        ("#f2ece1", "#e0d6c6", "#8c5a3c", "#2c211a", "#5c4a3a", "frame"),
    ]
    bg1, bg2, accent, tcol, acol, motif = schemes[abs(hash(seed or title)) % len(schemes)]
    cjk = has_cjk(title)
    if cjk:
        lines = [title] if len(title) <= 8 else [title[: len(title) // 2], title[len(title) // 2 :]]
    else:
        words = title.split()
        lines = [title] if len(title) <= 18 or len(words) < 3 else [
            " ".join(words[: len(words) // 2]),
            " ".join(words[len(words) // 2 :]),
        ]
    return {
        "palette": {"background": bg1, "background2": bg2, "accent": accent, "title": tcol, "author": acol},
        "layout": "centered",
        "motif": motif,
        "motif_intensity": "subtle",
        "typeface": "serif",
        "title_lines": lines,
        "subtitle": "",
        "author_line": author,
        "rationale": "Generated locally without the API.",
    }
