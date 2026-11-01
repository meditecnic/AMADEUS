#!/usr/bin/env python3
"""Merge the scan outputs into candidates.csv and the report tables for dispatch 159.

Inputs (under --out unless noted):
  py_symbols.csv, py_defense_density.csv, py_lockin.csv (from py_scan.py)
  knip.json, fe_facts.json, css_classes.csv, ruff-raw.json
  inventory/legacy_memory.csv (hand-curated; see README.md)
  --cov-fe: vitest coverage-final.json (istanbul format)
Outputs: candidates.csv, category_totals.csv, candidate_totals.csv, top15_by_category.csv,
defense_top20.csv, lockin_top15.csv, knip_deps.csv, ruff_summary.csv, spotcheck_sample.csv, summary.json.
README.md section "标签" states the label order.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

LABEL_ORDER = ["现在就能删", "等 v11 切默认后删", "要瘦身", "不动", "锁死实现", "待定"]
CAND_FIELDS = ["类别", "文件", "起止行", "符号名", "行数", "来源", "建议标签", "理由"]
LEGACY_CAT = "旧版记忆"


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def tracked_files(repo: Path) -> list[str]:
    res = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, encoding="utf-8", check=True)
    return [line for line in res.stdout.splitlines() if line]


def load_fe_coverage(path: Path | None, repo: Path) -> dict[str, list[tuple[int, int]]]:
    """Repo-relative file -> [(statement start line, hit count)] from istanbul coverage-final.json."""
    if not path or not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    out: dict[str, list[tuple[int, int]]] = {}
    for abs_path, info in data.items():
        try:
            rel = Path(abs_path).resolve().relative_to(repo.resolve()).as_posix()
        except ValueError:
            continue
        out[rel] = [(loc["start"]["line"], info.get("s", {}).get(sid, 0))
                    for sid, loc in info.get("statementMap", {}).items()]
    return out


def file_cov(stmts: list[tuple[int, int]] | None) -> str:
    if stmts is None:
        return "未测量"
    if not stmts:
        return "无语句"
    return "0%" if all(h == 0 for _, h in stmts) else "有覆盖"


def range_cov(stmts: list[tuple[int, int]] | None, start: int, end: int) -> str:
    if stmts is None:
        return "未测量"
    inside = [h for line, h in stmts if start <= line <= end]
    if not inside:
        return "无语句"
    return "0%" if all(h == 0 for h in inside) else "有覆盖"


def top_threshold(rows: list[dict], key: str, share: float = 0.2) -> float:
    """Value at the top `share` of the rows that have a positive value (at least one row)."""
    vals = sorted((float(r[key]) for r in rows if float(r[key]) > 0), reverse=True)
    if not vals:
        return float("inf")
    return vals[max(1, math.ceil(len(vals) * share)) - 1]


def item_name(item) -> str:
    return item.get("name", "") if isinstance(item, dict) else str(item)


TEXT_EXT = (".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".html", ".json", ".md", ".css", ".rs", ".toml",
            ".yml", ".yaml", ".txt", ".cfg", ".ini", ".sh", ".ps1", ".bat", ".iss", ".spec")


def load_texts(repo: Path) -> dict[str, list[str]]:
    """Tracked text files outside inventory/, as lines. Used for the literal reference check."""
    out: dict[str, list[str]] = {}
    for f in tracked_files(repo):
        if f.startswith("inventory/") or not f.endswith(TEXT_EXT):
            continue
        p = repo / f
        if p.stat().st_size > 2_000_000:
            continue
        try:
            out[f] = p.read_text(encoding="utf-8-sig").splitlines()
        except UnicodeDecodeError:
            continue
    return out


def literal_refs(texts: dict[str, list[str]], name: str, file: str, start: int, end: int,
                 plain: bool = False) -> tuple[int, int, str]:
    """Count literal hits of name outside the lines start..end of file.

    Returns (hits in non-test files, hits in test files, first examples). A word match, so comments
    and strings count too. plain=True is a substring match (for file names).
    """
    pat = re.compile(rf"\b{re.escape(name)}\b") if not plain else None
    n_src = n_test = 0
    ex: list[str] = []
    for rel, lines in texts.items():
        is_test = common.category_of(rel)[0] == common.TEST
        for i, line in enumerate(lines, 1):
            if rel == file and start <= i <= end:
                continue
            hit = (name in line) if plain else bool(pat.search(line))
            if not hit:
                continue
            if is_test:
                n_test += 1
            else:
                n_src += 1
                if len(ex) < 2:
                    ex.append(f"{rel}:{i}")
    return n_src, n_test, "、".join(ex)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".")
    ap.add_argument("--out", default="inventory/out")
    ap.add_argument("--cov-fe", help="vitest coverage-final.json")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    out = repo / args.out
    inv = repo / "inventory"

    sym_rows = read_csv(out / "py_symbols.csv")
    dens_rows = read_csv(out / "py_defense_density.csv")
    lock_be = read_csv(out / "py_lockin.csv")
    fe = json.loads((out / "fe_facts.json").read_text(encoding="utf-8"))
    knip = json.loads((out / "knip.json").read_text(encoding="utf-8"))
    css_rows = read_csv(out / "css_classes.csv")
    legacy = read_csv(inv / "legacy_memory.csv")
    fe_cov = load_fe_coverage(Path(args.cov_fe) if args.cov_fe else None, repo)
    texts = load_texts(repo)

    # --- category totals over tracked code files ----------------------------------------------
    file_lines: dict[str, int] = {}
    cat_tot: dict[str, dict] = defaultdict(lambda: {"files": 0, "total": 0, "code": 0})
    not_counted: list[str] = []
    for f in tracked_files(repo):
        if not common.is_code(f):
            continue
        cat, _ = common.category_of(f)
        total, code = common.line_counts((repo / f).read_text(encoding="utf-8-sig"), f)
        file_lines[f] = total
        if cat == common.NOT_COUNTED:
            not_counted.append(f)
        cat_tot[cat]["files"] += 1
        cat_tot[cat]["total"] += total
        cat_tot[cat]["code"] += code
    cat_rows = [{"类别": c, "文件数": v["files"], "总行数": v["total"], "代码行（近似）": v["code"]}
                for c, v in sorted(cat_tot.items())]
    write_csv(out / "category_totals.csv", cat_rows, ["类别", "文件数", "总行数", "代码行（近似）"])

    # --- legacy list: resolve ranges -----------------------------------------------------------
    sym_index = {(r["file"], r["qualname"]): r for r in sym_rows}

    def enclosing(file: str, line: int) -> dict:
        cands = [r for r in sym_rows if r["file"] == file and int(r["start"]) <= line <= int(r["end"])]
        if not cands:
            raise SystemExit(f"no enclosing symbol: {file}:{line}")
        return min(cands, key=lambda r: int(r["end"]) - int(r["start"]))

    branch_rows, symbol_legacy, file_legacy = [], {}, []
    for row in legacy:
        file, qual, start, end = row["file"], row["qualname"], row["start"], row["end"]
        if row["status"].endswith("分支"):
            if qual == "@enclosing":
                qual = enclosing(file, int(start))["qualname"]
            branch_rows.append({**row, "qualname": qual, "start": start, "end": end})
        elif not qual:
            file_legacy.append(row)
        else:
            if file.endswith(".py"):
                if sym_index.get((file, qual)) is None:
                    raise SystemExit(f"legacy symbol not in py_symbols: {file} {qual}")
            else:
                ex = next((x for x in fe["exports"] if x["file"] == file and x["name"] == qual), None)
                if ex is None:
                    raise SystemExit(f"legacy frontend export not found: {file} {qual}")
                row = {**row, "start": ex["start"], "end": ex["end"]}
            symbol_legacy[(file, qual)] = row

    candidates: list[dict] = []

    def add(cat, file, start, end, name, lines, source, label, reason):
        candidates.append({
            "类别": cat, "文件": file, "起止行": f"{start}-{end}" if start and end else "",
            "符号名": name, "行数": lines, "来源": source, "建议标签": label, "理由": reason,
        })

    # --- backend symbols --------------------------------------------------------------------------
    for r in sym_rows:
        if r["group"] not in ("app", "scripts"):
            continue
        key = (r["file"], r["qualname"])
        lg = symbol_legacy.get(key)
        cat = LEGACY_CAT if lg is not None else r["category"]
        dead = bool(r["vulture"]) and r["coverage"] == "0%"
        if r["protected"]:
            if dead or lg is not None:
                add(cat, r["file"], r["start"], r["end"], r["qualname"], r["lines"],
                    "protected", "不动", f"保护项：{r['protected']}")
            continue
        if dead and r["refs_app"] == "0" and not r["route"]:
            n_src, n_test, ex = literal_refs(texts, r["qualname"].split(".")[-1], r["file"],
                                             int(r["start"]), int(r["end"]))
            reason = "vulture 报未用；覆盖率 0；app 内无引用（名字统计）；不在保护项"
            if n_test:
                reason += f"；测试中有 {n_test} 处同名引用（删时需同改测试）"
            if n_src:
                add(cat, r["file"], r["start"], r["end"], r["qualname"], r["lines"],
                    "vulture+coverage0+refs0", "待定",
                    f"vulture 与覆盖率 0 交叉（强候选），但非测试文件仍有 {n_src} 处同名字面引用（{ex}），需人工核")
            elif lg is not None:
                # 旧版记忆整块等 v11 切默认后删（派单第四节、U 11-01 边界），死代码也不单独提前删
                reason += f"；属旧版记忆：{lg['status']}；按派单归「等 v11」"
                add(cat, r["file"], r["start"], r["end"], r["qualname"], r["lines"],
                    "vulture+coverage0+refs0", "等 v11 切默认后删", reason)
            else:
                add(cat, r["file"], r["start"], r["end"], r["qualname"], r["lines"],
                    "vulture+coverage0+refs0", "现在就能删", reason)
        elif dead:
            add(cat, r["file"], r["start"], r["end"], r["qualname"], r["lines"],
                "vulture+coverage0", "待定",
                f"强候选（vulture 与覆盖率 0 交叉），但 app 内仍有 {r['refs_app']} 处同名引用，需人工核")
        elif lg is not None:
            add(cat, r["file"], r["start"], r["end"], r["qualname"], r["lines"],
                "legacy-manual", lg["label"], lg["evidence"])
        elif r["vulture"]:
            add(cat, r["file"], r["start"], r["end"], r["qualname"], r["lines"],
                "vulture", "待定", f"vulture 报未用，但覆盖率为「{r['coverage']}」，规则套不上")

    # --- frontend legacy symbols (not in py_symbols) ----------------------------------------------
    for (file, qual), row in symbol_legacy.items():
        if file.endswith(".py"):
            continue
        add(LEGACY_CAT if "旧版" in row["status"] else common.category_of(file)[0], file, row["start"], row["end"],
            qual, int(row["end"]) - int(row["start"]) + 1, "legacy-manual", row["label"], row["evidence"])

    # --- legacy branches (only part of a function) and file-level legacy rows ----------------------
    for e in branch_rows:
        add(LEGACY_CAT, e["file"], e["start"], e["end"], f"{e['qualname']}（分支）",
            int(e["end"]) - int(e["start"]) + 1, "legacy-manual", e["label"], e["evidence"])
    for e in file_legacy:
        add(common.category_of(e["file"])[0], e["file"], "", "", "", file_lines.get(e["file"], 0),
            "legacy-manual", e["label"], e["evidence"])

    # --- backend file-level: defense density (要瘦身) and lock-in (锁死实现) ----------------------
    # 要瘦身 is a file-level decision (density or dup+swallow); the candidate rows are the symbols that
    # hold the defense items, so 行数 is the symbol's own line span, not the whole file.
    be_prod = [r for r in dens_rows if r["category"] == common.PRODUCT]
    be_thr = top_threshold(be_prod, "per_kloc")
    flagged_be: dict[str, str] = {}
    for r in be_prod:
        dup_swallow = int(r["dup_raise"]) + int(r["swallow"])
        top = float(r["per_kloc"]) > 0 and float(r["per_kloc"]) >= be_thr
        if top or dup_swallow >= 5:
            flagged_be[r["file"]] = "defense-density" if top else "dup+swallow>=5"
    sym_by_key = {(r["file"], r["qualname"]): r for r in sym_rows}
    be_items: dict = defaultdict(Counter)
    for d in read_csv(out / "py_defense_items.csv"):
        line = int(d["line"])
        cands = [s for s in sym_rows if s["file"] == d["file"] and int(s["start"]) <= line <= int(s["end"])]
        if not cands:
            continue
        sym = min(cands, key=lambda s: int(s["end"]) - int(s["start"]))
        # Protected hits are listed even when their file is not flagged for slimming (dispatch section 3).
        if d["file"] not in flagged_be and not sym["protected"]:
            continue
        be_items[(d["file"], sym["qualname"])][d["kind"]] += 1
    existing = {(c["文件"], c["符号名"]) for c in candidates}
    for (file, qual), kinds in sorted(be_items.items()):
        if (file, qual) in existing:
            continue
        parts = [f"{k} {kinds[k]}" for k in ("broad_except", "swallow", "getattr_default", "hasattr",
                                             "dup_raise", "compat_identifier", "compat_comment") if kinds[k]]
        if not parts:
            continue
        sym = sym_by_key[(file, qual)]
        judged = f"所在文件判定为{flagged_be[file]}" if file in flagged_be else "所在文件未达要瘦身阈值"
        why = "防御项：" + "、".join(parts) + "；" + judged
        if sym["protected"]:
            add(sym["category"], file, sym["start"], sym["end"], qual, sym["lines"],
                "protected", "不动", f"保护项：{sym['protected']}；{why}")
        else:
            add(sym["category"], file, sym["start"], sym["end"], qual, sym["lines"],
                flagged_be[file], "要瘦身", why)
    for r in lock_be:
        if r["label"] == "锁死实现":
            add(common.TEST, r["file"], "", "", "", int(r["lines"]), "lockin-density", "锁死实现",
                f"锁死实现写法 {r['total']} 次，{r['per_kloc']}/千行（阈值 10）")

    # --- frontend ---------------------------------------------------------------------------------
    fe_lines = {f["file"]: int(f["lines"]) for f in fe["files"]}
    fe_exports: dict = defaultdict(list)
    for ex in fe["exports"]:
        fe_exports[ex["file"]].append(ex)
    fe_def: dict = defaultdict(Counter)
    for d in fe["defense"]:
        fe_def[d["file"]][d["kind"]] += 1
    fe_prod = []
    for f, lines in fe_lines.items():
        if common.category_of(f)[0] != common.PRODUCT:
            continue
        c = fe_def[f]
        dens = c["catch_no_throw"] + c["as_any"] + c["optional_chain"] + c["nullish"]
        fe_prod.append({"file": f, "lines": lines, "dens_total": dens, "per_kloc": common.per_kloc(dens, lines),
                        "empty_catch": c["empty_catch"], "catch_no_throw": c["catch_no_throw"],
                        "as_any": c["as_any"], "optional_chain": c["optional_chain"], "nullish": c["nullish"]})
    fe_thr = top_threshold(fe_prod, "per_kloc")
    flagged_fe: dict[str, str] = {}
    for r in fe_prod:
        swallow = r["catch_no_throw"]
        top = r["per_kloc"] > 0 and r["per_kloc"] >= fe_thr
        if top or swallow >= 5:
            flagged_fe[r["file"]] = "fe-defense-density" if top else "fe-catch>=5"
    fe_stmt: dict = defaultdict(Counter)
    for d in fe["defense"]:
        if d["file"] in flagged_fe and d.get("stmt_start") and d["kind"] in ("catch_no_throw", "empty_catch", "as_any"):
            fe_stmt[(d["file"], int(d["stmt_start"]), int(d["stmt_end"]), d["stmt_name"])][d["kind"]] += 1
    for (file, s, e, name), kinds in sorted(fe_stmt.items()):
        parts = [f"{k} {kinds[k]}" for k in ("empty_catch", "catch_no_throw", "as_any") if kinds[k]]
        add(common.PRODUCT, file, s, e, name, e - s + 1, flagged_fe[file], "要瘦身",
            "防御项：" + "、".join(parts) + f"；所在文件判定为{flagged_fe[file]}")

    fe_lock = []
    for r in fe["lockin"]:
        total = r["vi_mock_internal"] + r["tohavebeencalled"]
        per = common.per_kloc(total, r["lines"])
        fe_lock.append({"file": r["file"], "lines": r["lines"], "total": total, "per_kloc": per})
        if per >= 10:
            add(common.TEST, r["file"], "", "", "", r["lines"], "lockin-density", "锁死实现",
                f"vi.mock 内部模块 {r['vi_mock_internal']} 次，调用断言 {r['tohavebeencalled']} 次，{per}/千行（阈值 10）")

    # knip: unused files and exports go to candidates; dependencies go to knip_deps.csv
    knip_deps = []
    seen: set = set()
    for issue in knip.get("issues", []):
        rel_file = "desktop/" + issue["file"] if issue.get("file") else ""
        if not rel_file:
            continue
        cat = common.category_of(rel_file)[0]
        for k in ("dependencies", "devDependencies", "unlisted", "binaries", "unresolved"):
            for item in issue.get(k, []) or []:
                knip_deps.append({"类型": k, "文件": rel_file, "名字": item_name(item)})
        if cat == common.TEST:
            continue
        for _ in issue.get("files", []) or []:
            if (rel_file, "file") in seen:
                continue
            seen.add((rel_file, "file"))
            stmts = fe_cov.get(rel_file)
            cov = file_cov(stmts)
            lines = file_lines.get(rel_file, fe_lines.get(rel_file, 0))
            n_src, n_test, nex = literal_refs(texts, Path(rel_file).stem, rel_file, 0, 10**9, plain=True)
            label = "现在就能删" if cov == "0%" else "待定"
            reason = f"knip 报未用文件；覆盖率 {cov}"
            if n_src:
                label = "待定"
                reason += f"；但非测试文件里有 {n_src} 处文件名引用（{nex}），knip 只看 import，需人工核"
            add(cat, rel_file, "", "", "", lines, "knip-file" + ("+coverage" if stmts is not None else ""),
                label, reason)
        for kind in ("exports", "types"):
            for item in issue.get(kind, []) or []:
                name = item_name(item)
                if (rel_file, kind, name) in seen:
                    continue
                seen.add((rel_file, kind, name))
                ex = next((e for e in fe_exports.get(rel_file, []) if e["name"] == name), None)
                if ex is None:
                    continue
                cov = range_cov(fe_cov.get(rel_file), int(ex["start"]), int(ex["end"]))
                label = "现在就能删" if cov == "0%" else "待定"
                reason = f"knip 报未用导出（{kind}）；导出覆盖率 {cov}"
                if kind == "types" or cov == "无语句":
                    label = "待定"
                    reason += "；类型或无运行时语句，覆盖率不能判定"
                n_src, n_test, nex = literal_refs(texts, name, rel_file, int(ex["start"]), int(ex["end"]))
                if n_src:
                    label = "待定"
                    reason += f"；但非测试文件仍有 {n_src} 处同名引用（{nex}），需人工核"
                elif n_test and label == "现在就能删":
                    reason += f"；测试中有 {n_test} 处同名引用（删时需同改测试）"
                add(cat, rel_file, ex["start"], ex["end"], name,
                    int(ex["end"]) - int(ex["start"]) + 1, f"knip-{kind}+coverage", label, reason)

    # Backend scripts: one 待定 row per file. Whether an evaluation script only serves the old memory path
    # is a judgement the rules cannot make (dispatch 四: 拿不准的进待定).
    sym_by_file: dict = defaultdict(list)
    for r in sym_rows:
        if r["group"] == "scripts":
            sym_by_file[r["file"]].append(r)
    for f, syms in sorted(sym_by_file.items()):
        cat, note = common.category_of(f)
        if cat not in (common.EVAL, common.PENDING_SCRIPT):
            continue
        zero = sum(1 for s in syms if s["coverage"] == "0%")
        add(cat, f, "", "", "", file_lines.get(f, 0), "script-file+coverage", "待定",
            f"{note}；{zero}/{len(syms)} 个函数或类覆盖率为 0；是否只为旧版服务未核，由总调度定")

    # CSS classes with no literal match: 待定 (false positives are expected), 行数 0
    for r in css_rows:
        if r["found"] == "no":
            add(common.category_of(r["css_file"])[0], r["css_file"], "", "", r["class"], 0,
                "css-literal", "待定", "CSS 类名字面搜不到；可能是拼出来的类名（误报），选择器行号未取")

    # --- write outputs ----------------------------------------------------------------------------
    def order(c):
        label = c["建议标签"]
        return (LABEL_ORDER.index(label) if label in LABEL_ORDER else 9, c["类别"], c["文件"], c["起止行"])

    cand_rows = sorted(candidates, key=order)
    write_csv(out / "candidates.csv", cand_rows, CAND_FIELDS)

    by_cat_label: dict = defaultdict(lambda: {"rows": 0, "lines": 0})
    by_label: dict = defaultdict(lambda: {"rows": 0, "lines": 0})
    for c in cand_rows:
        n = int(c["行数"] or 0)
        for bucket, key in ((by_cat_label, (c["类别"], c["建议标签"])), (by_label, c["建议标签"])):
            bucket[key]["rows"] += 1
            bucket[key]["lines"] += n
    write_csv(out / "candidate_totals.csv",
              [{"类别": k[0], "建议标签": k[1], "候选条数": v["rows"], "行数（候选）": v["lines"]}
               for k, v in sorted(by_cat_label.items())],
              ["类别", "建议标签", "候选条数", "行数（候选）"])

    top_rows = []
    for cat in sorted({c["类别"] for c in cand_rows}):
        per_file: Counter = Counter()
        for c in cand_rows:
            if c["类别"] == cat and c["建议标签"] in ("现在就能删", "等 v11 切默认后删", "要瘦身", "锁死实现"):
                per_file[c["文件"]] += int(c["行数"] or 0)
        for f, n in per_file.most_common(15):
            top_rows.append({"类别": cat, "文件": f, "候选行数": n})
    write_csv(out / "top15_by_category.csv", top_rows, ["类别", "文件", "候选行数"])

    be_top = [{"语言": "Python", "文件": r["file"], "行数": r["lines"], "防御次数": r["density_total"],
               "每千行": r["per_kloc"], "宽泛except": r["broad_except"], "吞异常": r["swallow"],
               "getattr默认": r["getattr_default"], "hasattr": r["hasattr"], "重复校验": r["dup_raise"],
               "兼容标记": int(r["compat_identifier"]) + int(r["compat_comment"])} for r in be_prod]
    fe_top = [{"语言": "TypeScript", "文件": r["file"], "行数": r["lines"], "防御次数": r["dens_total"],
               "每千行": r["per_kloc"], "宽泛except": "", "吞异常": r["catch_no_throw"],
               "getattr默认": "", "hasattr": "", "重复校验": "", "兼容标记": ""} for r in fe_prod]
    dens_all = sorted(be_top + fe_top, key=lambda r: -float(r["每千行"]))
    dens_fields = ["语言", "文件", "行数", "防御次数", "每千行", "宽泛except", "吞异常", "getattr默认", "hasattr",
                   "重复校验", "兼容标记"]
    write_csv(out / "defense_top20.csv", [r for r in dens_all if not common.protected_reason(r["文件"])][:20],
              dens_fields)
    write_csv(out / "defense_protected.csv",
              [{**r, "保护项": common.protected_reason(r["文件"])} for r in dens_all
               if common.protected_reason(r["文件"])],
              dens_fields + ["保护项"])

    lock_all = [{"语言": "Python", "文件": r["file"], "行数": r["lines"], "次数": r["total"], "每千行": r["per_kloc"]}
                for r in lock_be]
    lock_all += [{"语言": "TypeScript", "文件": r["file"], "行数": r["lines"], "次数": r["total"],
                  "每千行": r["per_kloc"]} for r in fe_lock]
    write_csv(out / "lockin_top15.csv", sorted(lock_all, key=lambda r: -float(r["每千行"]))[:15],
              ["语言", "文件", "行数", "次数", "每千行"])

    write_csv(out / "knip_deps.csv", knip_deps, ["类型", "文件", "名字"])

    ruff_path = out / "ruff-raw.json"
    ruff_rows = []
    if ruff_path.exists():
        cnt: Counter = Counter()
        for item in json.loads(ruff_path.read_text(encoding="utf-8")):
            rel = Path(item["filename"]).resolve().relative_to(repo).as_posix()
            grp = ("app" if rel.startswith("backend/app/") else "scripts" if rel.startswith("backend/scripts/")
                   else "tests" if rel.startswith("backend/tests/") else "other")
            cnt[(item["code"], grp)] += 1
        ruff_rows = [{"规则": k[0], "目录": k[1], "次数": v} for k, v in sorted(cnt.items())]
    write_csv(out / "ruff_summary.csv", ruff_rows, ["规则", "目录", "次数"])

    dead_rows = [c for c in cand_rows if c["建议标签"] == "现在就能删"]
    sample = random.Random(159).sample(dead_rows, min(10, len(dead_rows))) if dead_rows else []
    write_csv(out / "spotcheck_sample.csv", sample, CAND_FIELDS)

    summary = {
        "category_totals": cat_rows,
        "candidate_totals": [{"label": k, **v} for k, v in sorted(by_label.items())],
        "not_counted_code_files": not_counted,
        "dead_rows": len(dead_rows),
        "be_threshold_per_kloc": be_thr,
        "fe_threshold_per_kloc": fe_thr,
        "legacy_rows": len(legacy),
        "knip_issues": len(knip.get("issues", [])),
        "css_classes": len(css_rows),
        "css_not_found": sum(1 for r in css_rows if r["found"] == "no"),
    }
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in ("category_totals", "not_counted_code_files")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
