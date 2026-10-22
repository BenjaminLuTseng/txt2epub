"""Split a plain-text book into chapters.

For Chinese books, every occurrence of `第X章` is the chapter boundary. The
marker may be embedded in a line and may have release labels before it; those
labels are deliberately ignored. The resulting heading is always exactly
`第X章`.

Volume headings (第…卷) and front/back matter (序, 楔子, Prologue, ...) are
folded in alongside the winner rather than competing with it. Bare-number
headings are only trusted when the numbers actually ascend, since "1998" on its
own line is a year far more often than a chapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_CN_NUM = "零一二三四五六七八九十百千两〇壹贰叁肆伍陆柒捌玖拾０-９0-9"

# A heading is a short line; body prose that happens to start with 第一章 will
# be much longer than this.
MAX_HEADING_LEN = 60

# Real-world .txt headings often carry a section marker or brackets:
#   正文 第一章 初雪   /   【第一章】初雪   /   ★第一章 初雪
_PREFIX = r"(?:[【\[（(★☆◆◇※·\-—=＝\s　]*(?:正文|VIP|vip|卷[一二三四五六七八九十0-9]*)?[】\]）)\s　]*)?"
_TAIL = r"[ \t　]*[:：、.．\-—~～]?[ \t　]*(?P<title>.{0,50}?)[】\]）)]?$"

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "markdown",
        re.compile(r"^#{1,3}[ \t]+(?P<title>\S.*)$"),
    ),
    (
        # The definitive Chinese chapter unit. 章/回/節/折 only — volumes are a
        # separate, coarser level and must not dilute this family's count.
        "cn_chapter",
        re.compile(
            rf"^{_PREFIX}第[ \t]*[{_CN_NUM}]{{1,12}}[ \t]*[章回節节折](?![一-鿿]){_TAIL}"
        ),
    ),
    (
        "cn_volume",
        re.compile(
            rf"^{_PREFIX}(?:第[ \t]*[{_CN_NUM}]{{1,12}}[ \t]*[卷篇部集幕]"
            rf"|卷[ \t]*[{_CN_NUM}]{{1,6}})(?![一-鿿]){_TAIL}"
        ),
    ),
    (
        "en_chapter",
        re.compile(
            r"^(?:chapter|part|book|section)[ \t]+"
            r"(?:\d{1,4}|[ivxlcdm]{1,8}|one|two|three|four|five|six|seven|eight|nine|ten|"
            r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)"
            r"\b[ \t]*[:.\-—]?[ \t]*(?P<title>.{0,60})$",
            re.IGNORECASE,
        ),
    ),
    (
        "bare_number",
        re.compile(r"^(?P<num>\d{1,4})[ \t]*[、.．:：]?[ \t　]*(?P<title>.{0,50})$"),
    ),
]

SPECIAL = re.compile(
    r"^(?:序[章言幕曲]?|楔子|引子|引言|前言|自序|後記|后记|尾聲|尾声|終章|终章|"
    r"番外.{0,20}|附錄|附录|作者的話|作者的话|"
    r"prologue|epilogue|foreword|preface|afterword|introduction|appendix)"
    r"[ \t　]*[:：、.\-—]?[ \t　]*(.{0,50})$",
    re.IGNORECASE,
)


@dataclass
class Chapter:
    title: str
    paragraphs: list[str] = field(default_factory=list)
    include: bool = True

    @property
    def char_count(self) -> int:
        return sum(len(p) for p in self.paragraphs)

    @property
    def preview(self) -> str:
        text = " ".join(self.paragraphs)[:180]
        return text


def _candidate_lines(lines: list[str], pattern: re.Pattern[str]) -> list[int]:
    hits = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s or len(s) > MAX_HEADING_LEN:
            continue
        if pattern.match(s):
            hits.append(i)
    return hits


def _ascending(lines: list[str], idxs: list[int], pattern: re.Pattern[str]) -> bool:
    nums = []
    for i in idxs:
        m = pattern.match(lines[i].strip())
        if m and m.groupdict().get("num"):
            nums.append(int(m.group("num")))
    if len(nums) < 3:
        return False
    ascending = sum(1 for a, b in zip(nums, nums[1:]) if b > a)
    return ascending / (len(nums) - 1) > 0.9


def find_headings(lines: list[str], custom_regex: str | None = None) -> tuple[list[int], str]:
    """Return (heading line indices, name of the pattern that won)."""
    if custom_regex:
        pattern = re.compile(custom_regex, re.IGNORECASE | re.MULTILINE)
        return _candidate_lines(lines, pattern), "custom"

    found: dict[str, list[int]] = {}
    for name, pattern in PATTERNS:
        hits = _candidate_lines(lines, pattern)
        if len(hits) < 2:
            continue
        if name == "bare_number" and not _ascending(lines, hits, pattern):
            continue
        # A pattern matching a huge share of all lines is matching prose, not
        # headings. 第…章 is specific enough to trust at almost any density — a
        # densely-chaptered novel is a real thing and must not be rejected — so
        # only the loose families get a tight ceiling.
        ceiling = 0.9 if name in ("cn_chapter", "cn_volume") else 0.25
        if len(hits) > max(40, len(lines) * ceiling):
            continue
        found[name] = hits

    # 第…章 decides. A book that uses it has its chapter structure right there,
    # so it wins even when a looser family matched more lines.
    if len(found.get("cn_chapter", [])) >= 2:
        best_name, best_hits = "cn_chapter", found["cn_chapter"]
    elif found:
        best_name, best_hits = max(found.items(), key=lambda kv: len(kv[1]))
    else:
        best_name, best_hits = "", []

    # Once 第…章 exists, it is the only Chinese chapter signal. Do not add
    # labels such as 卷 or 序 as competing chapter entries.
    extra: set[int] = set() if best_name == "cn_chapter" else set(_candidate_lines(lines, SPECIAL))
    # Volume lines are a coarser level, not a rival: keep them as headings so a
    # 卷 divider gets its own entry instead of being swallowed by the chapter
    # above it.
    merged = sorted(set(best_hits) | extra)
    if not merged:
        return [], "none"
    return merged, best_name or "special_only"


def _drop_toc_block(chapters: list[Chapter]) -> tuple[list[Chapter], int]:
    """Discard a table-of-contents listing at the head of the file.

    Chinese .txt releases very often begin with every chapter title printed as
    a bare list. Those lines match the heading pattern perfectly, so the split
    produces a long run of empty chapters before the real book starts.
    """
    seen: dict[str, int] = {}
    for c in chapters:
        seen[c.title] = seen.get(c.title, 0) + 1

    # A listed title reappears later as the real chapter. Requiring the repeat
    # keeps genuine empty section dividers (第一卷 with no text of its own)
    # while still removing the listing.
    run = 0
    for c in chapters:
        if c.char_count < 30 and seen.get(c.title, 0) > 1:
            run += 1
        else:
            break
    if run >= 5 and run < len(chapters):
        return chapters[run:], run
    return chapters, 0


_TERMINAL = "。．.！!？?”』」…—〉》"


def _runs_suggest_wrapping(body: list[str]) -> bool:
    """True when the source hard-wraps one paragraph across several lines.

    Two formats have to be told apart, and they look identical if you only
    count consecutive non-empty lines:

    * Chinese/Japanese .txt, where every line is a complete paragraph and blank
      lines are optional — lines end on sentence-final punctuation and vary
      freely in length.
    * Gutenberg-style English, wrapped to a fixed column — lines break
      mid-sentence and cluster tightly just under the wrap width.
    """
    filled = [line.strip() for line in body if line.strip()]
    if len(filled) < 5:
        return False

    finished = sum(1 for line in filled if line[-1] in _TERMINAL)
    if finished / len(filled) >= 0.6:
        return False  # each line already ends a sentence: line == paragraph

    runs, current = [], 0
    for line in body:
        if line.strip():
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    if not runs or sum(runs) / len(runs) <= 1.6:
        return False

    lengths = sorted(len(line) for line in filled)
    wrap_width = lengths[int(len(lengths) * 0.95)]
    if wrap_width > 120:
        return False  # paragraph-length lines, not a wrap column
    near_full = sum(1 for n in lengths if n >= wrap_width * 0.8)
    return near_full / len(lengths) > 0.5


def to_paragraphs(body: list[str]) -> list[str]:
    if _runs_suggest_wrapping(body):
        paragraphs, buffer = [], []
        for line in body:
            if line.strip():
                buffer.append(line.strip())
            elif buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
        if buffer:
            paragraphs.append(" ".join(buffer))
        return paragraphs
    return [line.strip() for line in body if line.strip()]


_INLINE_CHAPTER = re.compile(
    rf"第(?P<num>[{_CN_NUM}]{{1,12}})章(?P<title>.{{1,50}})$"
)


def _cn_number(value: str) -> int | None:
    if value.isdigit() or all("０" <= ch <= "９" for ch in value):
        try:
            return int(value.translate(str.maketrans("０１２３４５６７８９", "0123456789")))
        except ValueError:
            return None
    digits = dict(zip("零〇一二兩两三四五六七八九", [0, 0, 1, 2, 2, 2, 3, 4, 5, 6, 7, 8, 9]))
    units = {"十": 10, "拾": 10, "百": 100, "千": 1000}
    total = 0
    section = 0
    number = 0
    for ch in value:
        if ch in digits:
            number = digits[ch]
        elif ch in units:
            unit = units[ch]
            section += (number or 1) * unit
            number = 0
        else:
            return None
    return total + section + number


def _repair_embedded_first_heading(lines: list[str]) -> list[str]:
    """Split every 第X章 marker, wherever it occurs in a source line.

    The text before a marker remains body text, while the marker becomes an
    exact heading. This intentionally discards labels such as 綠晨, 清心居,
    and upload-site boilerplate before the marker.
    """
    repaired: list[str] = []
    for line in lines:
        matches = list(re.finditer(rf"第(?P<num>[{_CN_NUM}]{{1,12}})章", line))
        if not matches:
            repaired.append(line)
            continue
        cursor = 0
        for match in matches:
            before = line[cursor : match.start()]
            if before.strip():
                repaired.append(before)
            repaired.append(f"第{match.group('num')}章")
            cursor = match.end()
        after = line[cursor:]
        if after.strip():
            repaired.append(after)
    return repaired


def split(
    lines: list[str], custom_regex: str | None = None, language: str = "en"
) -> tuple[list[Chapter], str]:
    cjk = language.startswith(("zh", "ja", "ko"))
    whole_label = "正文" if cjk else "Full Text"
    front_label = "前言" if cjk else "Front Matter"

    working_lines = _repair_embedded_first_heading(lines)
    idxs, method = find_headings(working_lines, custom_regex)

    if not idxs:
        body = to_paragraphs(working_lines)
        return [Chapter(title=whole_label, paragraphs=body)] if body else [], "none"

    chapters: list[Chapter] = []

    front = to_paragraphs(working_lines[: idxs[0]])
    # Tiny release metadata blocks (title, author, "正文", separators) are not
    # meaningful front matter. A slightly larger threshold prevents a 46-char
    # header from becoming a bogus 前言 chapter while preserving real prefaces.
    if front and sum(len(p) for p in front) > 80:
        chapters.append(Chapter(title=front_label, paragraphs=front))

    bounds = idxs + [len(working_lines)]
    for start, end in zip(bounds, bounds[1:]):
        title = working_lines[start].strip().lstrip("#").strip()
        paragraphs = to_paragraphs(working_lines[start + 1 : end])
        chapters.append(Chapter(title=title or "(untitled)", paragraphs=paragraphs))

    chapters, dropped = _drop_toc_block(chapters)
    if dropped:
        method += f" (dropped {dropped}-entry contents list)"

    # Keep any front matter in place, but force all detected 第X章 entries into
    # numeric order even when the source file places them out of order.
    numbered = []
    for pos, chapter in enumerate(chapters):
        match = re.fullmatch(rf"第(?P<num>[{_CN_NUM}]{{1,12}})章", chapter.title)
        if match:
            number = _cn_number(match.group("num"))
            if number is not None:
                numbered.append((pos, number, chapter))
    if len(numbered) >= 2:
        ordered = sorted(numbered, key=lambda item: item[1])
        for (pos, _, _), (_, _, chapter) in zip(numbered, ordered):
            chapters[pos] = chapter

    return chapters, method
