#!/usr/bin/env python3
"""Print what the detector sees in a file, without building anything.

    python3 diagnose.py ~/Downloads/一劍蕩魔.txt
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from txt2epub import chapters as chap, ingest


def main(path: str) -> None:
    doc = ingest.load(Path(path).read_bytes())
    print(f"file      {path}")
    print(f"encoding  {doc.encoding}")
    print(f"language  {doc.language}")
    print(f"size      {doc.char_count:,} chars over {len(doc.lines):,} lines\n")

    print("-- how each pattern scored --")
    for name, pattern in chap.PATTERNS:
        hits = chap._candidate_lines(doc.lines, pattern)
        print(f"   {name:<14} {len(hits):>6} lines")
    print(f"   {'special':<14} {len(chap._candidate_lines(doc.lines, chap.SPECIAL)):>6} lines")

    idxs, method = chap.find_headings(doc.lines)
    chapters, method = chap.split(doc.lines, language=doc.language)
    print(f"\n-- winner: {method} → {len(chapters)} chapters --")

    for c in chapters[:12]:
        print(f"   {c.char_count:>7,}  {c.title[:56]}")
    if len(chapters) > 24:
        print(f"   ... {len(chapters) - 24} more ...")
    for c in chapters[max(12, len(chapters) - 12):]:
        print(f"   {c.char_count:>7,}  {c.title[:56]}")

    empty = [c for c in chapters if c.char_count < 30]
    if empty:
        print(f"\n-- {len(empty)} suspiciously short chapters --")
        for c in empty[:10]:
            print(f"   {c.char_count:>7,}  {c.title[:56]}")

    print("\n-- first 30 non-empty lines of the file --")
    shown = 0
    for i, line in enumerate(doc.lines):
        if line.strip():
            marker = "H>" if i in set(idxs) else "  "
            print(f"   {marker} {i:>6}| {line[:80]}")
            shown += 1
            if shown >= 30:
                break


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
