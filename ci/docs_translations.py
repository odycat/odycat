#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only OR LicenseRef-OdyCAT-Commercial
# Copyright (c) 2026 Ocean Octane S.L.U.
"""Which translated documentation pages are behind their English text.

docs/<language>/<page>.md translates docs/<page>.md. Its first line records
the English text it was translated from, as a git blob id:

    <!-- Translation of docs/<page>.md. English text translated: git blob <id> ... -->

This compares that id with the English page as it is now and lists the pages
whose English changed. After bringing a translation up to date, put the new
id it prints in that first line (or run it with --mark <language>/<page>.md).

    python3 ci/docs_translations.py            # list (exit 0)
    python3 ci/docs_translations.py --diff     # and the English diff, when git has both
    python3 ci/docs_translations.py --strict   # exit 1 if any page is behind
    python3 ci/docs_translations.py --mark es/getting-started.md
"""

import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

DOCS = Path(__file__).resolve().parents[1] / "docs"
LANGUAGES = ["es"]
HEADER = re.compile(r"English text translated: git blob ([0-9a-f]{40})")


def blob_id(path: Path) -> str:
    """The id git gives the file's content (git hash-object), without git."""
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def english_of(page: Path) -> Path:
    return DOCS / page.name


def mark(rel: str) -> None:
    page = DOCS / rel
    text = page.read_text(encoding="utf-8")
    new = blob_id(english_of(page))
    if not HEADER.search(text):
        sys.exit(f"{rel}: no 'English text translated: git blob' header on its first line")
    page.write_text(HEADER.sub(f"English text translated: git blob {new}", text, count=1),
                    encoding="utf-8")
    print(f"{rel}: marked as translated from {new}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--diff", action="store_true", help="print the English diff of each page behind")
    ap.add_argument("--strict", action="store_true", help="exit 1 if a page is behind")
    ap.add_argument("--mark", nargs="+", metavar="LANG/PAGE.md",
                    help="record the current English text as translated")
    a = ap.parse_args()
    if a.mark:
        for rel in a.mark:
            mark(rel)
        return 0

    behind = 0
    for lang in LANGUAGES:
        for page in sorted((DOCS / lang).glob("*.md")):
            rel = f"{lang}/{page.name}"
            english = english_of(page)
            if not english.is_file():
                print(f"{rel}: docs/{page.name} no longer exists")
                behind += 1
                continue
            m = HEADER.search(page.read_text(encoding="utf-8").split("\n", 1)[0])
            now = blob_id(english)
            if not m:
                print(f"{rel}: no 'English text translated: git blob' header on its first line")
                behind += 1
            elif m.group(1) != now:
                behind += 1
                print(f"{rel}: docs/{page.name} changed since it was translated "
                      f"(translated {m.group(1)[:12]}, now {now})")
                if a.diff:
                    r = subprocess.run(["git", "diff", "--no-color", m.group(1), now],
                                       capture_output=True, text=True, cwd=DOCS)
                    print(r.stdout if r.returncode == 0 and r.stdout
                          else "   (git does not have the translated version: compare by hand)")
            else:
                print(f"{rel}: up to date")
    if behind:
        print(f"{behind} page(s) behind the English text. Once updated, put the new blob id "
              f"in the page's first line, or: python3 ci/docs_translations.py --mark LANG/PAGE.md")
    return 1 if behind and a.strict else 0


if __name__ == "__main__":
    sys.exit(main())
