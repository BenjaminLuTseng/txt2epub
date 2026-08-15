"""Decode a .txt file of unknown encoding and normalise it into clean text.

Chinese plain-text ebooks in the wild are GB18030, Big5 or UTF-8, usually with
no BOM and often mislabelled. Rather than trusting a single detector we decode
with every plausible codec and score the results: genuine Chinese text draws
heavily on a few hundred very common characters, while a wrong CJK codec
produces a soup of rare ideographs. That ratio separates them cleanly.
"""

from __future__ import annotations

import codecs
import re
import unicodedata
from dataclasses import dataclass

# The most frequent characters in simplified and traditional Chinese. Real text
# covers a large fraction of this set; mojibake essentially never does.
_COMMON_SIMPLIFIED = (
    "的一是了我不人在他有这个上们来到时大地为子中你说生国年着就那和要她出也得里后自以"
    "会家可下而过天去能对小多然于心学么之都好看起发当没成只如事把还用第样道想作种开美"
    "总从无情己面最女但现前些所同日手又行意动方期它头经长儿回位分爱老因很给名法间知世"
    "什两次使身者被高已亲其进此话常与活正感"
)
_COMMON_TRADITIONAL = (
    "們這來時個國學會後說對於發過麼萬點爲產經體實現關樣頭長無愛親車東馬語話買賣讀寫聽"
    "見開門問題還與從當隻總處務準辦種義許將軍書氣聲區區內兩戰師實應該還沒麼樣覺得裡邊"
    "說話當然變樣師傅劍靈魔氣勢陣訣煉輩隨憐術術術龍鳳靈識靈氣體內經脈遠遠遠處聽見來說"
)
_COMMON = set(_COMMON_SIMPLIFIED + _COMMON_TRADITIONAL)

_TRAD_ONLY = set("們這來時國學會後說對發過麼萬點產經體實現關樣頭長無愛親車東馬語話買賣讀寫聽見開門問題還與從個")
_SIMP_ONLY = set("们这来时国学会后说对发过么万点产经体实现关样头长无爱亲车东马语话买卖读写听见开门问题还与从个")

_BOMS = (
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xff\xfe\x00\x00", "utf-32-le"),
    (b"\x00\x00\xfe\xff", "utf-32-be"),
    (b"\xff\xfe", "utf-16-le"),
    (b"\xfe\xff", "utf-16-be"),
)

# Ordered by how much of the world's .txt they cover. gb18030 will "succeed" on
# almost any byte string, so it never wins on decodability alone — only on score.
# The BOM-less UTF-16 variants matter: Windows Notepad's "Unicode" save produces
# them, and every 8-bit codec turns them into NUL-riddled garbage.
_CANDIDATES = (
    "utf-8",
    "gb18030",
    "big5hkscs",
    "utf-16-le",
    "utf-16-be",
    "shift_jis",
    "euc-kr",
    "cp1252",
    "latin-1",
)


@dataclass
class Document:
    text: str
    lines: list[str]
    encoding: str
    language: str  # BCP-47: zh-Hans, zh-Hant, ja, ko, en
    char_count: int

    @property
    def is_cjk(self) -> bool:
        return self.language.startswith(("zh", "ja", "ko"))


def _score(text: str) -> float:
    """Higher is more likely to be a correct decoding."""
    n = len(text)
    if n == 0:
        return -1e9
    cjk = 0
    common = 0
    bad = 0
    ascii_ok = 0
    for ch in text:
        if "一" <= ch <= "鿿":
            cjk += 1
            if ch in _COMMON:
                common += 1
        elif ch in _COMMON:
            common += 1
        if ch in "\n\r\t":
            ascii_ok += 1
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Co", "Cn", "Cs"):
            bad += 1
        elif " " <= ch <= "~":
            ascii_ok += 1

    score = -60.0 * (bad / n)
    if cjk / n > 0.03:
        # Chinese/Japanese text: judge by how ordinary the characters are.
        score += 100.0 * (common / cjk)
    else:
        score += 45.0 * (ascii_ok / n)
    return score


def decode(data: bytes) -> tuple[str, str]:
    """Return (text, encoding-name) for a bytes blob of unknown encoding."""
    for bom, enc in _BOMS:
        if data.startswith(bom):
            return data.decode(enc, errors="replace"), enc

    # A clean strict UTF-8 decode is decisive — no other codec produces valid
    # UTF-8 multibyte sequences by accident at any length.
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass

    # BOM-less UTF-16 is unmistakable from NUL placement, and scoring can't see
    # it because an odd-length sample makes both variants decode to noise.
    head = data[:8000]
    nul = head.count(0)
    if nul > len(head) * 0.2:
        even = sum(1 for i in range(0, len(head) - 1, 2) if head[i] == 0)
        odd = sum(1 for i in range(1, len(head), 2) if head[i] == 0)
        enc = "utf-16-be" if even > odd else "utf-16-le"
        return data.decode(enc, errors="replace"), enc + " (no BOM)"

    best: tuple[float, str, str] | None = None
    for enc in _CANDIDATES:
        text = _decode_sample(data, enc)
        if text is None:
            continue
        s = _score(text)
        if best is None or s > best[0]:
            best = (s, enc, text)

    if best is None:
        return data.decode("utf-8", errors="replace"), "utf-8 (with errors)"

    _, enc, _ = best
    return data.decode(enc, errors="replace"), enc


_SAMPLE_BYTES = 400_000


def _decode_sample(data: bytes, enc: str) -> str | None:
    """Decode a prefix of `data`, tolerating a character split by the cut.

    Truncating a byte sample almost always lands mid-character in a multibyte
    encoding. A plain strict `bytes.decode` then raises, the *correct* codec is
    discarded, and scoring is left to choose between codecs that can never
    fail — cp1252 and latin-1 happily decode anything, so Chinese text ends up
    as mojibake. An incremental decoder with `final=False` holds the dangling
    tail bytes instead of raising, while still rejecting a codec that genuinely
    cannot read the content.
    """
    try:
        decoder = codecs.getincrementaldecoder(enc)(errors="strict")
        return decoder.decode(data[:_SAMPLE_BYTES], final=False)
    except (UnicodeDecodeError, LookupError):
        return None


def score_candidates(data: bytes) -> list[tuple[str, float, str]]:
    """Every codec's score and a text sample — for the diagnostics endpoint."""
    out = []
    for enc in _CANDIDATES:
        text = _decode_sample(data, enc)
        if text is None:
            out.append((enc, float("-inf"), "<cannot decode this content>"))
            continue
        out.append((enc, round(_score(text), 2), text[:120].replace("\n", "⏎")))
    return sorted(out, key=lambda row: -row[1])


def detect_language(text: str) -> str:
    sample = text[:200_000]
    han = sum(1 for c in sample if "一" <= c <= "鿿")
    kana = sum(1 for c in sample if "぀" <= c <= "ヿ")
    hangul = sum(1 for c in sample if "가" <= c <= "힣")
    total = max(len(sample), 1)

    if hangul / total > 0.05:
        return "ko"
    if kana / total > 0.02:
        return "ja"
    if han / total > 0.05:
        trad = sum(1 for c in sample if c in _TRAD_ONLY)
        simp = sum(1 for c in sample if c in _SIMP_ONLY)
        return "zh-Hant" if trad > simp * 1.2 else "zh-Hans"
    return "en"


_CONTROL_OK = {"\n", "\t"}


def normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("﻿", "")
    text = "".join(
        ch for ch in text if ch in _CONTROL_OK or unicodedata.category(ch)[0] != "C"
    )
    out = []
    for line in text.split("\n"):
        # Leading full-width spaces are the source file's indentation; the
        # stylesheet owns indentation in the EPUB, so strip them here.
        out.append(line.strip("　  \t"))
    text = "\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


def load(data: bytes) -> Document:
    raw, encoding = decode(data)
    text = normalise(raw)
    return Document(
        text=text,
        lines=text.split("\n"),
        encoding=encoding,
        language=detect_language(text),
        char_count=len(text),
    )
