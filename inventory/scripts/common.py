"""Shared rules for the 159 code inventory.

README.md documents the same rules. Change a rule here and in README.md together.
"""

from __future__ import annotations

import re

PRODUCT = "产品代码"
EVAL = "评测/实验脚本"
TEST = "测试"
PENDING_SCRIPT = "待定（脚本）"
NOT_COUNTED = "未纳入四类"

# Only these extensions count as source code. JSON, Markdown, HTML, SVG, TOML, data and binaries are not counted.
CODE_EXT = (".py", ".ts", ".tsx", ".js", ".mjs", ".css", ".rs")

# Test code: backend/tests, packaging/windows/tests, desktop/src/test (vitest setup and helpers),
# and any *.test.* file under desktop/. The last two go beyond the dispatch's list; see README.md.
TEST_DIRS = ("backend/tests/", "packaging/windows/tests/", "desktop/src/test/")
TEST_NAME = re.compile(r"\.test\.[cm]?[jt]sx?$")

# backend/scripts/ is classified file by file. Names not listed here fall to PENDING_SCRIPT.
BACKEND_SCRIPTS = {
    "_path_guards.py": (EVAL, "评测脚本共用的路径守卫"),
    "_smoke_tts_preflight.py": (PENDING_SCRIPT, "TTS 预检，是否只给实跑用未核"),
    "build_memory_candidate.py": (PENDING_SCRIPT, "记忆候选构建，用途未核"),
    "legacy_core_facts_rehearsal.py": (EVAL, "迁移预演（rehearsal）"),
    "legacy_memory_migrate.py": (PENDING_SCRIPT, "旧版记忆迁移工具，U 的数据可能仍要用，待定"),
    "load_sovits_weights.py": (PENDING_SCRIPT, "TTS 权重加载，实跑还是产品未核"),
    "memory_candidate_real_run.py": (EVAL, "实跑（real_run）"),
    "memory_candidate_runtime.py": (PENDING_SCRIPT, "运行时模块，是否被产品导入未核"),
    "memory_recall_b5_real_run.py": (EVAL, "实跑（real_run）"),
    "memory_recall_b5c.py": (EVAL, "召回评测（按文件名判断）"),
    "memory_recall_offline_run.py": (EVAL, "离线评测（offline_run）"),
    "memory_supersede_probe.py": (EVAL, "探针（probe）"),
    "persona_eval_run.py": (EVAL, "人格评测（eval_run）"),
    "purge_mistaken_demo_seed.py": (PENDING_SCRIPT, "清理误加的 demo 数据，属开发维护还是评测未定"),
    "seed_memory_demo.py": (PENDING_SCRIPT, "demo 数据种子，属开发日常还是评测未定"),
}

# Protected items (dispatch section 三). They are listed separately and never get a delete or slim label.
PROTECTED_FILES = {
    "backend/app/services/credentials.py": "凭据存取",
    "backend/app/services/memory_v11/privacy.py": "打码",
    "backend/app/services/conversation_erasure.py": "删除传播的校验",
    "backend/app/services/memory_v11/migration.py": "数据库迁移（派单保护项，整文件从严）",
    "packaging/windows/backend_entry.py": "令牌和端口校验（整文件从严）",
    "packaging/windows/license_bundle.py": "许可核对",
    "packaging/windows/source_materials.py": "构建来源检查（下载与哈希核对）",
    "desktop/src-tauri/src/lib.rs": "启动链（整文件）",
}
PROTECTED_PREFIXES = {
    "backend/app/security/": "安全链（Origin、Host、令牌）",
}
PROTECTED_SYMBOLS = {
    "packaging/windows/bundle_support.py": {
        "digest": "哈希核对",
        "verify_frontend_assets": "前端资产核对",
        "license_inventory": "许可核对",
    },
}


def category_of(path: str) -> tuple[str, str]:
    """Return (category, reason) for a repo-relative path."""
    if path.startswith(TEST_DIRS) or (path.startswith("desktop/") and TEST_NAME.search(path)):
        return TEST, "测试目录或 *.test.* 文件"
    if path.startswith("desktop/src/dev-mock/"):
        return EVAL, "dev-mock（派单第一节第 2 类）"
    if path.startswith("backend/scripts/"):
        return BACKEND_SCRIPTS.get(path.rsplit("/", 1)[1], (PENDING_SCRIPT, "未在映射表里"))
    if path.startswith("desktop/scripts/"):
        return PRODUCT, "开发启动器或端口脚本，派单归产品"
    if path.startswith(("backend/app/", "desktop/src/", "desktop/src-tauri/src/", "packaging/windows/")):
        return PRODUCT, "产品目录"
    return NOT_COUNTED, "不在四类路径里"


def is_code(path: str) -> bool:
    return path.endswith(CODE_EXT)


def protected_reason(path: str, symbol: str | None = None) -> str | None:
    if path in PROTECTED_FILES:
        return PROTECTED_FILES[path]
    for prefix, reason in PROTECTED_PREFIXES.items():
        if path.startswith(prefix):
            return reason
    if symbol is not None:
        reason = PROTECTED_SYMBOLS.get(path, {}).get(symbol.split(".")[-1])
        if reason:
            return reason
        if path == "backend/app/db.py" and re.search(r"migrat|schema|upgrade", symbol, re.IGNORECASE):
            return "数据库迁移（派单保护项）"
    return None


def line_counts(text: str, path: str) -> tuple[int, int]:
    """Return (total_lines, code_lines).

    total_lines is the wc -l style count (blank and comment lines included). This is the unit
    used for every density ("次数 / 千行") and for candidate line counts.
    code_lines is an approximation: non-blank lines that are not pure comment lines.
    """
    lines = text.splitlines()
    total = len(lines)
    code = 0
    in_block = False
    is_py = path.endswith(".py")
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        if is_py:
            if not s.startswith("#"):
                code += 1
            continue
        if in_block:
            if "*/" in s:
                in_block = False
            continue
        if s.startswith("//"):
            continue
        if s.startswith("/*"):
            if "*/" not in s:
                in_block = True
            continue
        code += 1
    return total, code


def per_kloc(count: int, total_lines: int) -> float:
    return round(count * 1000.0 / total_lines, 2) if total_lines else 0.0
