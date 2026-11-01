#!/usr/bin/env python3
"""CSS class-name check for the 159 inventory.

Takes every class selector in desktop/src/**/*.css and searches the text of desktop/src/**/*.ts and *.tsx
for the same name as a literal. Names built at run time (template strings, concatenation) will show up as
false positives; README.md says so. Writes <out>/css_classes.csv.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

COMMENT = re.compile(r"/\*.*?\*/", re.S)
URL = re.compile(r"url\([^)]*\)", re.I)
STRING = re.compile(r"\"[^\"\n]*\"|'[^'\n]*'")
# A class selector: a dot not preceded by a digit or another dot, then a name that starts with a letter,
# underscore, or hyphen. This skips decimals such as 1.5em and .2s.
CLASS = re.compile(r"(?<![\d.])\.(-?[A-Za-z_][\w-]*)")


def selector_classes(css: str) -> list[str]:
    body = STRING.sub('""', URL.sub("url()", COMMENT.sub("", css)))
    return sorted(set(CLASS.findall(body)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="inventory/out")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    out = repo / args.out
    out.mkdir(parents=True, exist_ok=True)

    src = repo / "desktop" / "src"
    sources = []
    for p in sorted(src.rglob("*")):
        if p.suffix in {".ts", ".tsx"}:
            rel = p.relative_to(repo).as_posix()
            sources.append((rel, p.read_text(encoding="utf-8-sig")))

    rows = []
    for css_path in sorted(src.rglob("*.css")):
        css_rel = css_path.relative_to(repo).as_posix()
        for name in selector_classes(css_path.read_text(encoding="utf-8-sig")):
            pat = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(name) + r"(?![A-Za-z0-9_-])")
            hits_product = hits_test = 0
            for rel, text in sources:
                n = len(pat.findall(text))
                if not n:
                    continue
                if common.category_of(rel)[0] == common.TEST:
                    hits_test += n
                else:
                    hits_product += n
            rows.append({
                "css_file": css_rel, "class": name, "hits_product": hits_product, "hits_test": hits_test,
                "found": "yes" if hits_product + hits_test else "no",
            })

    with (out / "css_classes.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["css_file", "class", "hits_product", "hits_test", "found"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    not_found = sum(1 for r in rows if r["found"] == "no")
    print(f"css files={len({r['css_file'] for r in rows})} classes={len(rows)} not_found={not_found}")


if __name__ == "__main__":
    main()
