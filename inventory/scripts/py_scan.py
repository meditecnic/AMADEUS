#!/usr/bin/env python3
"""Backend AST scans for the 159 inventory. Reads the repo; writes only under inventory/.

Phase 1, before vulture (writes the vulture whitelist):
    python inventory/scripts/py_scan.py --phase whitelist
Phase 2, after vulture and coverage have run:
    python inventory/scripts/py_scan.py --phase analyze \
        --coverage <runs>/cov/coverage.json --vulture inventory/out/vulture-app.txt
README.md states the rule behind each column.
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import json
import re
import sys
import tokenize
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

GROUPS = {
    "app": "backend/app",
    "scripts": "backend/scripts",
    "tests": "backend/tests",
    "pkg": "packaging/windows",  # non-test packaging code; its tests are loaded as pkg_tests
    "pkg_tests": "packaging/windows/tests",
}
# Kinds that make up the defense density (次数 / 千行). swallow is a subset of broad_except and
# unused_kwarg is a heuristic with many false positives, so both are reported but not summed.
DENSITY_KINDS = ("broad_except", "getattr_default", "hasattr", "dup_raise", "compat_identifier", "compat_comment")
ROUTE_ATTRS = {
    "get", "post", "put", "patch", "delete", "api_route", "websocket", "on_event", "middleware",
    "exception_handler", "field_validator", "model_validator", "validator", "field_serializer",
    "model_serializer", "computed_field",
}
LOG_METHODS = {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
COMPAT_RE = re.compile(r"legacy|compat|fallback|backward|deprecated|(?<![A-Za-z0-9])old_", re.IGNORECASE)
VULTURE_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+): unused (?P<kind>[a-z ]+?) '(?P<name>[^']+)' "
    r"\((?P<conf>\d+)% confidence(?:, (?P<lines>\d+) lines?)?\)"
)
LOCKIN_KEYS = ("私有导入", "monkeypatch私有", "调用断言", "读源码文本", "私有属性断言")


def is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def is_private(name: str) -> bool:
    return name.startswith("_") and not is_dunder(name)


def call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def deco_names(node) -> set[str]:
    names = set()
    for d in getattr(node, "decorator_list", []):
        t = d.func if isinstance(d, ast.Call) else d
        if isinstance(t, ast.Attribute):
            names.add(t.attr)
        elif isinstance(t, ast.Name):
            names.add(t.id)
    return names


def str_const(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def is_broad(t) -> bool:
    """True for bare except, Exception, BaseException, and tuples that contain them."""
    if t is None:
        return True
    if isinstance(t, ast.Name):
        return t.id in {"Exception", "BaseException"}
    if isinstance(t, ast.Attribute):
        return t.attr in {"Exception", "BaseException"}
    if isinstance(t, ast.Tuple):
        return any(is_broad(e) for e in t.elts)
    return False


def is_log_stmt(s: ast.stmt) -> bool:
    if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)):
        return False
    f = s.value.func
    if isinstance(f, ast.Name):
        return f.id == "print"
    if isinstance(f, ast.Attribute):
        if f.attr in {"print_exc", "print_exception"}:
            return True
        base = f.value
        bname = base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else ""
        return f.attr in LOG_METHODS and "log" in bname.lower()
    return False


def swallows(handler: ast.ExceptHandler) -> bool:
    """A handler swallows when it never re-raises and every statement is pass, continue, return, or a log call."""
    if any(isinstance(n, ast.Raise) for n in ast.walk(handler)):
        return False
    return all(isinstance(s, (ast.Pass, ast.Continue, ast.Return)) or is_log_stmt(s) for s in handler.body)


def short(rel: str) -> str:
    return rel[len("backend/"):] if rel.startswith("backend/") else rel


def load(repo: Path) -> dict:
    files = {}
    for group, rel_dir in GROUPS.items():
        for path in sorted((repo / rel_dir).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if group == "pkg" and "tests" in path.relative_to(repo / rel_dir).parts:
                continue
            rel = path.relative_to(repo).as_posix()
            text = path.read_text(encoding="utf-8-sig")  # some files start with a BOM; ast.parse rejects it
            try:
                tree = ast.parse(text, filename=rel)
                toks = list(tokenize.generate_tokens(io.StringIO(text).readline))
            except (SyntaxError, tokenize.TokenError) as exc:
                print(f"skip {rel}: {exc}", file=sys.stderr)
                continue
            files[rel] = {"group": group, "text": text, "tree": tree, "toks": toks}
    return files


def symbols_of(rel: str, group: str, tree: ast.Module) -> list[dict]:
    """Module-level functions and classes, plus methods of module-level classes (private ones included)."""
    out = []

    def add(node, qual: str, kind: str) -> None:
        body = list(node.body)
        if body and isinstance(body[0], ast.Expr) and str_const(body[0].value) is not None:
            body = body[1:]  # skip the docstring
        stmt_lines = set()
        for stmt in body:
            for n in ast.walk(stmt):
                if isinstance(n, ast.stmt):
                    stmt_lines.add(n.lineno)
        start = min([node.lineno] + [d.lineno for d in node.decorator_list])
        out.append({
            "file": rel, "group": group, "qualname": qual, "name": node.name, "kind": kind,
            "start": start, "end": node.end_lineno, "stmt_lines": stmt_lines, "decorators": deco_names(node),
        })

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node, node.name, "function")
        elif isinstance(node, ast.ClassDef):
            add(node, node.name, "class")
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(sub, f"{node.name}.{sub.name}", "method")
    return out


def repo_attr_names(files: dict) -> set[str]:
    """Names that repo classes define: methods, class attributes, and self.<name> stores (heuristic)."""
    names = set()
    for f in files.values():
        if f["group"] not in ("app", "scripts"):
            continue
        for n in ast.walk(f["tree"]):
            if isinstance(n, ast.ClassDef):
                for sub in n.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        names.add(sub.name)
                    elif isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                        names.add(sub.target.id)
                    elif isinstance(sub, ast.Assign):
                        names.update(t.id for t in sub.targets if isinstance(t, ast.Name))
            if (isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store)
                    and isinstance(n.value, ast.Name) and n.value.id == "self"):
                names.add(n.attr)
    return names


def kw_names(files: dict) -> set[str]:
    """Every keyword name passed in any call in the repo (app, scripts, tests)."""
    used = set()
    for f in files.values():
        for n in ast.walk(f["tree"]):
            if isinstance(n, ast.Call):
                used.update(kw.arg for kw in n.keywords if kw.arg)
    return used


class Defense(ast.NodeVisitor):
    """Collects defense items for one file. Each item carries the enclosing function or class."""

    def __init__(self, rel: str, used_kw: set[str], repo_attrs: set[str]):
        self.rel = rel
        self.used_kw = used_kw
        self.repo_attrs = repo_attrs
        self.scope: list[str] = []
        self.items: list[dict] = []
        self.raise_ifs: list[tuple[str, int, str]] = []

    def _add(self, line: int, kind: str, detail: str) -> None:
        self.items.append({
            "file": self.rel, "line": line, "kind": kind,
            "symbol": self.scope[-1] if self.scope else "<module>", "detail": detail[:120],
        })

    def _enter(self, node) -> None:
        qual = f"{self.scope[-1]}.{node.name}" if self.scope else node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._unused_params(node, qual)
        self.scope.append(qual)
        self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _enter
    visit_AsyncFunctionDef = _enter
    visit_ClassDef = _enter

    def _unused_params(self, node, qual: str) -> None:
        if deco_names(node) & ROUTE_ATTRS:
            return  # FastAPI and pydantic hooks receive their arguments from the framework
        a = node.args
        off = len(a.args) - len(a.defaults)
        names = [p.arg for i, p in enumerate(a.args) if i >= off]
        names += [p.arg for p, d in zip(a.kwonlyargs, a.kw_defaults) if d is not None]
        for name in names:
            if name not in {"self", "cls"} and name not in self.used_kw:
                self.items.append({"file": self.rel, "line": node.lineno, "kind": "unused_kwarg",
                                   "symbol": qual, "detail": name})

    def visit_ExceptHandler(self, node) -> None:
        if is_broad(node.type):
            self._add(node.lineno, "broad_except", "bare" if node.type is None else ast.unparse(node.type))
            if swallows(node):
                self._add(node.lineno, "swallow", "吞异常（无重新抛出）")
        self.generic_visit(node)

    def _with(self, node) -> None:
        for item in node.items:
            ce = item.context_expr
            if isinstance(ce, ast.Call) and call_name(ce) == "suppress" and ce.args and is_broad(ce.args[0]):
                self._add(node.lineno, "broad_except", "contextlib.suppress(Exception)")
                self._add(node.lineno, "swallow", "suppress 吞异常")
        self.generic_visit(node)

    visit_With = _with
    visit_AsyncWith = _with

    def visit_Call(self, node) -> None:
        name = call_name(node)
        if name == "getattr" and len(node.args) == 3:
            attr = str_const(node.args[1]) or "?"
            scope = "本仓类属性" if attr in self.repo_attrs else "外部"
            self._add(node.lineno, "getattr_default", f"{attr}|{scope}")
        elif name == "hasattr" and len(node.args) == 2:
            attr = str_const(node.args[1]) or "?"
            scope = "本仓类属性" if attr in self.repo_attrs else "外部"
            self._add(node.lineno, "hasattr", f"{attr}|{scope}")
        self.generic_visit(node)

    def visit_If(self, node) -> None:
        if not node.orelse and len(node.body) == 1 and isinstance(node.body[0], ast.Raise):
            sym = self.scope[-1] if self.scope else "<module>"
            self.raise_ifs.append((ast.dump(node), node.lineno, sym))
        self.generic_visit(node)


def compat_items(rel: str, toks: list, syms: list[dict]) -> list[dict]:
    out = []
    for t in toks:
        if t.type not in (tokenize.NAME, tokenize.COMMENT) or not COMPAT_RE.search(t.string):
            continue
        line = t.start[0]
        enclosing = [s for s in syms if s["start"] <= line <= s["end"]]
        symbol = min(enclosing, key=lambda s: s["end"] - s["start"])["qualname"] if enclosing else "<module>"
        kind = "compat_comment" if t.type == tokenize.COMMENT else "compat_identifier"
        out.append({"file": rel, "line": line, "kind": kind, "symbol": symbol, "detail": t.string[:120]})
    return out


def lockin_counts(f: dict) -> Counter:
    c: Counter = Counter()
    tree = f["tree"]
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom):
            c["私有导入"] += sum(1 for a in n.names if is_private(a.name))
        elif isinstance(n, ast.Import):
            c["私有导入"] += sum(1 for a in n.names if any(is_private(p) for p in a.name.split(".")))
        elif isinstance(n, ast.Call):
            fn = n.func
            if isinstance(fn, ast.Attribute):
                if (isinstance(fn.value, ast.Name) and fn.value.id == "monkeypatch"
                        and fn.attr in {"setattr", "delattr", "setitem", "delitem"} and n.args):
                    target = str_const(n.args[0])
                    attr = str_const(n.args[1]) if len(n.args) > 1 else None
                    if (attr and is_private(attr)) or (target and any(is_private(p) for p in target.split(".")[1:])):
                        c["monkeypatch私有"] += 1
                if (fn.attr.startswith(("assert_called", "assert_awaited"))
                        or fn.attr in {"assert_any_call", "assert_has_calls", "assert_any_await", "assert_has_awaits"}):
                    c["调用断言"] += 1
            if call_name(n) in {"getsource", "getsourcelines", "getsourcefile"}:
                c["读源码文本"] += 1
        elif isinstance(n, ast.Assert):
            if any(isinstance(x, ast.Attribute) and is_private(x.attr) for x in ast.walk(n)):
                c["私有属性断言"] += 1
    # read_text / open on a source file path, counted once per simple statement
    for n in ast.walk(tree):
        if isinstance(n, (ast.Assign, ast.Expr, ast.Assert, ast.Return, ast.AugAssign, ast.AnnAssign)):
            has_read = any(isinstance(x, ast.Call) and call_name(x) in {"read_text", "read_bytes", "open"}
                           for x in ast.walk(n))
            has_src = any(isinstance(x, ast.Constant) and isinstance(x.value, str)
                          and x.value.endswith((".py", ".ts", ".tsx", ".css", ".mjs")) for x in ast.walk(n))
            if has_read and has_src:
                c["读源码文本"] += 1
    return c


def privacy_names(tree: ast.Module) -> set[str]:
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module:
            if n.module.split(".")[-1] == "privacy":
                names.update(a.asname or a.name for a in n.names)
            elif n.module.endswith("memory_v11"):
                names.update(a.asname or a.name for a in n.names if a.name == "privacy")
        elif isinstance(n, ast.Import):
            for a in n.names:
                if a.name.split(".")[-1] == "privacy":
                    names.add(a.asname or a.name.split(".")[-1])
    return names


def load_coverage(path: str | None) -> dict | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for key, info in data["files"].items():
        k = key.replace("\\", "/")
        if "backend/" in k:
            k = k.split("backend/", 1)[1]
        out[k] = set(info.get("executed_lines", []))
    return out


def load_vulture(path: str | None) -> dict:
    hits: dict = defaultdict(list)
    if not path or not Path(path).exists():
        return hits
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        m = VULTURE_RE.match(raw)
        if m:
            hits[(short(m["file"]), m["name"])].append(
                (int(m["line"]), m["kind"], int(m["conf"]), int(m["lines"] or 0)))
    return hits


def fn_status(sym: dict, cov: dict | None) -> str:
    if cov is None:
        return "未测量"
    executed = cov.get(short(sym["file"]))
    if executed is None:
        return "未测量"
    if not sym["stmt_lines"]:
        return "无语句"
    return "执行过" if sym["stmt_lines"] & executed else "0%"


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def write_whitelist(repo: Path, files: dict) -> None:
    entries = []
    for rel, f in files.items():
        if f["group"] != "app":
            continue
        for n in ast.walk(f["tree"]):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and deco_names(n) & ROUTE_ATTRS:
                entries.append(f"{n.name}  # route or validator: {rel}:{n.lineno}")
            if isinstance(n, ast.ClassDef) and any(
                    (isinstance(b, ast.Name) and b.id == "BaseModel")
                    or (isinstance(b, ast.Attribute) and b.attr == "BaseModel") for b in n.bases):
                for sub in n.body:
                    if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name):
                        entries.append(f"_.{sub.target.id}  # pydantic field: {rel}:{sub.lineno} ({n.name})")
    header = [
        '"""Vulture whitelist for backend/app (dispatch 159).',
        "",
        "Generated by inventory/scripts/py_scan.py --phase whitelist. Each line marks a name as used:",
        "FastAPI route handlers, pydantic validators, and pydantic model fields. The app never imports this file.",
        '"""',
        "",
    ]
    text = "\n".join(header + sorted(set(entries))) + "\n"
    (repo / "inventory" / "vulture_whitelist.py").write_text(text, encoding="utf-8")
    print(f"whitelist entries: {len(set(entries))}")


def analyze(repo: Path, files: dict, out: Path, coverage_path: str | None, vulture_path: str | None) -> None:
    cov = load_coverage(coverage_path)
    vul = load_vulture(vulture_path)
    used_kw = kw_names(files)
    repo_attrs = repo_attr_names(files)

    use: dict = {g: Counter() for g in GROUPS}
    defs: dict = {g: Counter() for g in GROUPS}
    for f in files.values():
        g = f["group"]
        for t in f["toks"]:
            if t.type == tokenize.NAME:
                use[g][t.string] += 1
        for n in ast.walk(f["tree"]):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defs[g][n.name] += 1

    def refs(group: str, name: str) -> int:
        return max(0, use[group][name] - defs[group][name])

    symbols: list[dict] = []
    defense: list[dict] = []
    dup_cands: list[tuple[str, str, int, str]] = []
    lock_rows: list[dict] = []
    density_rows: list[dict] = []
    priv_lines: dict = {}

    for rel, f in files.items():
        g = f["group"]
        tree = f["tree"]
        if g in ("app", "scripts", "pkg"):
            syms = symbols_of(rel, g, tree)
            symbols += syms
            v = Defense(rel, used_kw, repo_attrs)
            v.visit(tree)
            items = v.items
            items += compat_items(rel, f["toks"], syms)
            defense += items
            dup_cands += [(rel, key, line, sym) for key, line, sym in v.raise_ifs]
            names = privacy_names(tree)
            if names:
                priv_lines[rel] = sorted(t.start[0] for t in f["toks"] if t.type == tokenize.NAME and t.string in names)
        if g in ("tests", "pkg_tests"):
            c = lockin_counts(f)
            total, code = common.line_counts(f["text"], rel)
            total_hits = sum(c.values())
            cat, _ = common.category_of(rel)
            lock_rows.append({
                "file": rel, "category": cat, "lines": total, "code_lines": code,
                **{k: c.get(k, 0) for k in LOCKIN_KEYS},
                "total": total_hits, "per_kloc": common.per_kloc(total_hits, total),
                "label": "锁死实现" if common.per_kloc(total_hits, total) >= 10 else "",
            })

    # Duplicate validation: the same if-raise structure, 3 or more times across app, scripts and packaging.
    dup_count = Counter(key for _, key, _, _ in dup_cands)
    for rel, key, line, sym in dup_cands:
        if dup_count[key] >= 3:
            defense.append({"file": rel, "line": line, "kind": "dup_raise", "symbol": sym,
                            "detail": f"同结构 if-raise 共 {dup_count[key]} 处"})

    # Per-file defense density (app, scripts and packaging). Density counts DENSITY_KINDS only.
    by_file_kind: dict = defaultdict(Counter)
    for d in defense:
        by_file_kind[d["file"]][d["kind"]] += 1
    for rel, f in files.items():
        if f["group"] not in ("app", "scripts", "pkg"):
            continue
        total, code = common.line_counts(f["text"], rel)
        k = by_file_kind[rel]
        dens_total = sum(k[x] for x in DENSITY_KINDS)
        cat, _ = common.category_of(rel)
        density_rows.append({
            "file": rel, "category": cat, "lines": total, "code_lines": code,
            "broad_except": k["broad_except"], "swallow": k["swallow"],
            "getattr_default": k["getattr_default"], "hasattr": k["hasattr"], "dup_raise": k["dup_raise"],
            "compat_identifier": k["compat_identifier"], "compat_comment": k["compat_comment"],
            "unused_kwarg": k["unused_kwarg"], "density_total": dens_total,
            "per_kloc": common.per_kloc(dens_total, total),
        })

    # Symbols: references, coverage, vulture, protection.
    by_class_methods: dict = defaultdict(list)
    for s in symbols:
        if s["kind"] == "method":
            by_class_methods[(s["file"], s["qualname"].split(".")[0])].append(s)
    for s in symbols:
        if s["kind"] in ("function", "method"):
            s["cov"] = fn_status(s, cov)
    for s in symbols:
        if s["kind"] == "class":
            ms = by_class_methods.get((s["file"], s["name"]), [])
            if not ms:
                s["cov"] = "无方法"
            elif all(m["cov"] == "0%" for m in ms):
                s["cov"] = "0%"
            else:
                s["cov"] = "有执行" if cov is not None else "未测量"
    sym_rows = []
    for s in symbols:
        s["refs_app"] = refs("app", s["name"])
        s["refs_scripts"] = refs("scripts", s["name"])
        s["refs_tests"] = refs("tests", s["name"]) + refs("pkg_tests", s["name"])
        hits = [h for h in vul.get((short(s["file"]), s["name"]), []) if s["start"] <= h[0] <= s["end"]]
        s["vulture"] = f"unused {hits[0][1]} ({hits[0][2]}%)" if hits else ""
        reason = common.protected_reason(s["file"], s["qualname"])
        if not reason and s["file"] in priv_lines:
            if any(s["start"] <= ln <= s["end"] for ln in priv_lines[s["file"]]):
                reason = "打码调用处"
        s["protected"] = reason or ""
        s["route"] = "yes" if s["decorators"] & ROUTE_ATTRS else ""
        cat, _ = common.category_of(s["file"])
        sym_rows.append({
            "category": cat, "file": s["file"], "qualname": s["qualname"], "kind": s["kind"],
            "start": s["start"], "end": s["end"], "lines": s["end"] - s["start"] + 1, "group": s["group"],
            "refs_app": s["refs_app"], "refs_scripts": s["refs_scripts"], "refs_tests": s["refs_tests"],
            "vulture": s["vulture"], "coverage": s["cov"], "protected": s["protected"], "route": s["route"],
        })

    fields_sym = ["category", "file", "qualname", "kind", "start", "end", "lines", "group",
                  "refs_app", "refs_scripts", "refs_tests", "vulture", "coverage", "protected", "route"]
    write_csv(out / "py_symbols.csv", sym_rows, fields_sym)
    only_tests = [r for r in sym_rows if r["group"] == "app" and r["refs_app"] == 0
                  and (r["refs_scripts"] + r["refs_tests"]) > 0]
    write_csv(out / "py_only_tests_or_scripts.csv", only_tests, fields_sym)
    zero = [r for r in sym_rows if r["coverage"] == "0%"]
    write_csv(out / "py_zero_coverage.csv", zero, fields_sym)

    fields_def = ["file", "line", "kind", "symbol", "detail"]
    write_csv(out / "py_defense_items.csv", sorted(defense, key=lambda d: (d["file"], d["line"])), fields_def)
    fields_dens = ["file", "category", "lines", "code_lines", "broad_except", "swallow", "getattr_default",
                   "hasattr", "dup_raise", "compat_identifier", "compat_comment", "unused_kwarg",
                   "density_total", "per_kloc"]
    density_rows.sort(key=lambda r: (-r["per_kloc"], r["file"]))
    write_csv(out / "py_defense_density.csv", density_rows, fields_dens)
    lock_rows.sort(key=lambda r: (-r["per_kloc"], r["file"]))
    write_csv(out / "py_lockin.csv", lock_rows,
              ["file", "category", "lines", "code_lines", *LOCKIN_KEYS, "total", "per_kloc", "label"])

    summary = {"groups": {}}
    for g in GROUPS:
        gfiles = [f for f in files.values() if f["group"] == g]
        totals = [common.line_counts(f["text"], "x.py") for f in gfiles]
        gsyms = [r for r in sym_rows if r["group"] == g]
        summary["groups"][g] = {
            "files": len(gfiles),
            "total_lines": sum(t[0] for t in totals),
            "code_lines": sum(t[1] for t in totals),
            "symbols": len(gsyms),
            "zero_coverage": sum(1 for r in gsyms if r["coverage"] == "0%"),
            "vulture_hits": sum(1 for r in gsyms if r["vulture"]),
        }
    summary["defense_kinds"] = dict(Counter(d["kind"] for d in defense))
    summary["coverage_loaded"] = cov is not None
    summary["vulture_loaded"] = bool(vul)
    (out / "py_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=".", help="worktree root")
    ap.add_argument("--out", default="inventory/out", help="output dir, relative to --repo")
    ap.add_argument("--phase", choices=["whitelist", "analyze"], required=True)
    ap.add_argument("--coverage", help="coverage.json from pytest-cov (keys relative to backend/)")
    ap.add_argument("--vulture", help="vulture text output")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    out = repo / args.out
    out.mkdir(parents=True, exist_ok=True)
    files = load(repo)
    if args.phase == "whitelist":
        write_whitelist(repo, files)
    else:
        analyze(repo, files, out, args.coverage, args.vulture)


if __name__ == "__main__":
    main()
