"""Split a plain-text book into chapters.

Strategy: run every heading pattern we know over the file, then pick a winner.
`第…章` wins outright whenever it appears in any quantity — in a Chinese book
that construction *is* the chapter structure, and letting a looser pattern
outvote it on raw count is how you end up splitting a novel on its page
numbers. Every other family competes on how many lines it explains.

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

    extra: set[int] = set(_candidate_lines(lines, SPECIAL))
    # Volume lines are a coarser level, not a rival: keep them as headings so a
    # 卷 divider gets its own entry instead of being swallowed by the chapter
    # above it.
    if best_name == "cn_chapter":
        extra |= set(found.get("cn_volume", []))

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


def split(
    lines: list[str], custom_regex: str | None = None, language: str = "en"
) -> tuple[list[Chapter], str]:
    cjk = language.startswith(("zh", "ja", "ko"))
    whole_label = "正文" if cjk else "Full Text"
    front_label = "前言" if cjk else "Front Matter"

    idxs, method = find_headings(lines, custom_regex)

    if not idxs:
        body = to_paragraphs(lines)
        return [Chapter(title=whole_label, paragraphs=body)] if body else [], "none"

    chapters: list[Chapter] = []

    front = to_paragraphs(lines[: idxs[0]])
    if front and sum(len(p) for p in front) > 40:
        chapters.append(Chapter(title=front_label, paragraphs=front))

    bounds = idxs + [len(lines)]
    for start, end in zip(bounds, bounds[1:]):
        title = lines[start].strip().lstrip("#").strip()
        paragraphs = to_paragraphs(lines[start + 1 : end])
        chapters.append(Chapter(title=title or "(untitled)", paragraphs=paragraphs))

    chapters, dropped = _drop_toc_block(chapters)
    if dropped:
        method += f" (dropped {dropped}-entry contents list)"

    return chapters, method
