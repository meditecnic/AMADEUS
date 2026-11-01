# 代码盘点（派单 159）

只盘点，不改产品代码、测试、配置或锁文件。结果由总调度抽查、定标签。

- 派单：`amadeus-ops/dispatch/159-code-inventory.md`；报告：`amadeus-ops/reports/159-code-inventory.md`（10 KB 以内）。
- 候选总表：`out/candidates.csv`（482 行，列：类别、文件、起止行、符号名、行数、来源、建议标签、理由）。其它细表与原始输出都在 `out/`。

## 基线与分支

- AMADEUS main：`ecb05136757d890f7119260d77b550b6351198e1`（即 origin/main，PR #35 的合并提交）。
- 分支：`claude/code-inventory-20261101`（派单「基线」节）。派单「交付」节写的是 `codex/code-inventory-20261101`，两处不一致。本次按「基线」节建分支，待总调度确认。
- 本分支不合并、不开 PR。本单只往 `inventory/` 加文件。

## 环境（Ubuntu，Factory droid）

| 工具 | 版本 | 来源 |
|---|---|---|
| Python | 3.11.17 | 本单 venv：`~/amadeus-work/runs/159-code-inventory/venv` |
| pytest、pytest-timeout | 9.1.1、2.4.0 | `backend/requirements-dev.lock`（`--require-hashes`） |
| ruff | 0.17.2 | `backend/requirements-dev.lock` 与 AGENTS.md |
| vulture | 2.16 | 本单 venv 另装（锁文件里没有） |
| pytest-cov、coverage | 7.1.0、7.17.0 | 本单 venv 另装（锁文件里没有） |
| Node.js、npm | 22.23.3、10.9.9 | 系统 |
| typescript | 5.8.3 | `desktop/package-lock.json` |
| vitest | 4.1.10 | `desktop/package-lock.json` |
| @vitest/coverage-v8 | 4.1.10 | `npm i --no-save`，与 vitest 同版本 |
| knip | 6.44.1 | `npm i --no-save`。仓库没有 knip 配置，用默认设置 |

`package.json`、`package-lock.json`、`requirements*`、`backend/requirements-dev.lock` 没有改动（git 核对过）。主树 `~/amadeus-work/AMADEUS` 和它的 `.venv` 没有动。

测试隔离：`AMADEUS_DATA_DIR` 设为 `~/amadeus-work/runs/159-code-inventory/data-pytest`，不设 `AMADEUS_DB_PATH`。`backend/tests/conftest.py` 的隔离保持不变。中间产物（pytest 临时目录、coverage JSON、junit、vitest 覆盖率 JSON）都在 `~/amadeus-work/runs/159-code-inventory/`，不进仓库。

## 命令（按顺序执行）

`RUN=~/amadeus-work/runs/159-code-inventory`，`VPY=$RUN/venv/bin/python`。每条都在仓库根目录执行。

```sh
# 0. 环境
$VPY -m pip install --require-hashes -r backend/requirements-dev.lock
$VPY -m pip install ruff==0.17.2 vulture==2.16 pytest-cov==7.1.0 coverage==7.17.0
(cd desktop && npm ci && npm i --no-save knip@6.44.1 @vitest/coverage-v8@4.1.10)

# 1. 后端白名单与 vulture
$VPY inventory/scripts/py_scan.py --repo . --phase whitelist
(cd backend && $VPY -m vulture app ../inventory/vulture_whitelist.py --min-confidence 60 > ../inventory/out/vulture-app.txt)

# 2. 后端测试与覆盖率（app 和 scripts 在一次 pytest 里测，再按文件拆分）
(cd backend && env -u AMADEUS_DB_PATH AMADEUS_DATA_DIR=$RUN/data-pytest PYTHONDONTWRITEBYTECODE=1 \
  COVERAGE_FILE=$RUN/cov/.coverage $VPY -m pytest -q -rfEsx -p no:cacheprovider \
  --basetemp $RUN/pytest-tmp/run --junitxml $RUN/out/junit-backend.xml \
  --cov=app --cov=scripts --cov-report=json:$RUN/cov/coverage.json)
$VPY inventory/scripts/py_scan.py --repo . --phase analyze --coverage $RUN/cov/coverage.json --vulture inventory/out/vulture-app.txt
(cd backend && $VPY -m ruff check --no-cache --select ARG,BLE,S110,S112,TRY,C901 --output-format json app scripts tests > ../inventory/out/ruff-raw.json)
# ruff 写出的 filename 带本机仓库路径前缀。提交前已去掉这个前缀（字符串替换，内容不变）。

# 3. 前端
(cd desktop && npx vitest run --maxWorkers=2 --reporter=default --coverage.enabled=true \
  --coverage.provider=v8 --coverage.reporter=json --coverage.reportsDirectory=$RUN/cov-fe \
  --coverage.include='src/**/*.{ts,tsx}')
(cd desktop && npx knip --reporter json > ../inventory/out/knip.json)   # 退出码 1 是正常的，表示有发现
node inventory/scripts/fe_scan.mjs --repo . --out inventory/out
$VPY inventory/scripts/css_scan.py --repo . --out inventory/out

# 4. 合并：候选、标签、汇总表
$VPY inventory/scripts/merge.py --repo . --out inventory/out --cov-fe $RUN/cov-fe/coverage-final.json
```

## 四类（每个文件只归一类）

- **产品代码**：`backend/app/`；`desktop/src/`（不含测试和 `dev-mock/`）；`desktop/src-tauri/src/`；`packaging/windows/`（不含 `tests/`）；`desktop/scripts/` 里的开发启动器和端口脚本（派单写明开发启动器归产品）。
- **评测/实验脚本**：`desktop/src/dev-mock/`（派单点名）；`backend/scripts/` 中按文件名判为评测、实跑、迁移预演、探针的脚本。映射表在 `scripts/common.py` 的 `BACKEND_SCRIPTS`。
- **待定（脚本）**：`backend/scripts/` 中归不准的脚本（映射表里写了原因）。
- **旧版记忆**：产品代码里的函数级子集，不单独成文件。清单在 `legacy_memory.csv`。它的行数已计入产品代码的文件行数，合计时不重复加。
- **测试**：`backend/tests/`、`packaging/windows/tests/`、`desktop/src/**/*.test.*`。另外两处是本单的判定，派单没列：`desktop/src/test/`（vitest 的 setup 和测试辅助）和 `desktop/scripts/*.test.*`，都归为测试。
- **未纳入四类**：不在以上路径里的代码文件（如 `desktop/src-tauri/build.rs`、`desktop/vite.config.ts`、`desktop/vitest.config.ts`、`.github/scripts/test_summary.py`、`desktop/public/` 下的生成字体样式表）。

## 计数口径

- **行数**：文件的 `wc -l` 口径，含空行和注释。所有密度（「次数 / 千行」）和候选行数都用这个口径。
- **代码行（近似）**：只在 `category_totals.csv` 的第 4 列出现。去掉空行和纯注释行，是近似值。
- **候选行数**：各候选行的行数之和。同一段代码可能被两条规则命中（例如类和它的方法），合计会略高。
- **代码文件**：`.py`、`.ts`、`.tsx`、`.js`、`.mjs`、`.css`、`.rs`。JSON、Markdown、HTML、SVG、TOML 和数据文件不计行数。
- **符号行数**：从定义行（含装饰器）到结束行。

## 后端规则（`scripts/py_scan.py`）

- **符号**：`backend/app`、`backend/scripts` 和 `packaging/windows`（非测试）的模块级函数、模块级类，以及模块级类的方法（含私有方法）。
- **引用数**：对标识符做词法统计（tokenize，不统计字符串和注释），减去定义处。按名字计，同名不同对象会合并，所以引用数偏高。
- **覆盖率**：函数或方法的语句行（不含文档字符串）在 pytest 中一行都没执行，记为 0%。类：所有方法都为 0% 才记为 0%。不在 coverage.json 里的文件记为「未测量」（`packaging/windows` 不在覆盖范围内）。
- **vulture**：`vulture app ../inventory/vulture_whitelist.py --min-confidence 60`，只跑 `app`。白名单由 AST 生成，共 178 条：117 个 pydantic 字段，61 个路由和 validator（`inventory/vulture_whitelist.py`）。pytest fixture 不在 vulture 范围内（只扫 `app`）。
- **防御项**（参与密度的类型）：
  - 宽泛 except：`Exception`、`BaseException`、裸 `except:`，以及元组里含前两者；`contextlib.suppress(Exception)`。
  - 吞异常（宽泛 except 的子集，单列）：处理块里没有 `raise`，且语句只有 pass、continue、return 或日志调用。
  - getattr 带默认值（三参数）；hasattr。是否「本仓类属性」是启发式判断。
  - 重复校验：AST 结构相同的 `if …: raise …` 在 app、scripts、packaging 里出现 3 次及以上，每一处都计入。
  - 兼容标记：标识符或注释里含 legacy、compat、fallback、backward、deprecated、`old_`（前面不是字母或数字）。
  - 密度 = 上述防御项（不含吞异常子集）÷ 千行。
- **没人用的参数**（`unused_kwarg`，单列，不计入密度）：带默认值的参数，其参数名在全仓的任何调用里都没有作为关键字参数出现。启发式，位置传参和基类覆盖的方法都会误报。路由和 validator 已排除。
- **锁死实现**（测试）：私有导入（`_` 开头的名字）、monkeypatch 私有属性、调用断言（`assert_called*`、`assert_awaited*`、`assert_any_call`、`assert_has_calls` 等）、读源码文本（`inspect.getsource`，以及用 read_text 或 open 读 `.py`/`.ts`/`.tsx`/`.css`/`.mjs` 文件）、对 `_` 开头属性的断言。每千行 10 次及以上记为「锁死实现」。
- **ruff**：`ruff check --no-cache --select ARG,BLE,S110,S112,TRY,C901 --output-format json`，只读，不改配置。规则计数见 `ruff_summary.csv`，按规则和目录（app、scripts、tests）统计。
- **编码**：读文件一律 UTF-8。少数文件以 BOM 开头，用 `utf-8-sig` 读，否则 `ast.parse` 会失败。

## 前端规则（`scripts/fe_scan.mjs`、`scripts/css_scan.py`、knip、vitest）

- **文件**：`desktop/src` 和 `desktop/scripts` 下的 `.ts`、`.tsx`（不含 `.d.ts`）。`desktop/scripts` 里的 `.mjs`（`dev-real.mjs`、`backend-port.mjs`）只计行数，不做防御扫描。
- **防御项**：空 catch；不重新抛的 catch（块里没有 throw）；`as any` 和 `<any>`；`?.` 和 `??` 计数。
- **密度** = （不重新抛的 catch + as any + `?.` + `??`）÷ 千行。`?.` 和 `??` 只进密度，不单列为候选，所以 TS 的密度通常高于后端。
- **导出**：顶层的 export 函数、类、变量、类型、接口、枚举，`export {…}` 和默认导出。
- **锁死实现**（测试）：`vi.mock` 的内部模块（相对路径）和调用断言（`toHaveBeenCalled*`）。每千行 10 次及以上记为「锁死实现」。
- **knip**：默认设置，退出码 1。36 个文件级问题条目，合计 70 个未用导出、45 个未用类型、1 个未用文件（`status-preview.tsx`，后被抽查否决，见下文）、2 个 `duplicates`（`ForgetConversationModal.tsx`、`SettingsModal.tsx` 的具名与默认导出重复，不是死代码，未计入候选）。**未用依赖：0 条**（`knip_deps.csv` 只有表头）。
- **vitest 覆盖率**：`@vitest/coverage-v8`，provider v8，`coverage.include=src/**/*.{ts,tsx}`，输出 istanbul 格式的 `coverage-final.json`。导出的覆盖率取其语句范围内的命中数；`desktop/scripts` 不计入覆盖率。
- **CSS**：派单写 9 个 `.css`，`desktop/src` 下确有 9 个。其中 `motion.css`、`tokens.css` 没有类选择器（只有变量、关键帧和属性选择器），实际扫描 7 个文件，共 826 个类选择器。类名在 `.ts`、`.tsx` 里做字面搜索（前后不能是字母、数字、`_`、`-`）。搜不到的 32 个记为待定。

## 标签（按优先级）

1. **不动**：
   - 保护项（派单第三节）：`backend/app/security/` 整目录；`services/credentials.py`；`services/memory_v11/privacy.py`；`services/conversation_erasure.py`；`services/memory_v11/migration.py`（整文件从严）；`packaging/windows/backend_entry.py`、`license_bundle.py`、`source_materials.py`（整文件）；`packaging/windows/bundle_support.py` 里的 `digest`、`verify_frontend_assets`、`license_inventory`；`backend/app/db.py` 里名字含 migrat、schema、upgrade 的函数；调用 privacy 名字的函数（打码调用处）。`desktop/src-tauri/src/lib.rs` 只做标注（Rust 不扫描），CSP 在配置文件里，不在本单的代码行范围内。
   - 保护项中有死代码或防御命中的，全部标「不动」，不论所在文件是否判为要瘦身。
   - 共用项（派单点名）：`EmbeddingAdapter`、`RetrievedMemory`、`memory_mode`、`_require_v11_memory_write`。
2. **现在就能删**：同时满足：
   - vulture 报未用（后端），或 knip 报未用（前端导出或文件）；
   - 覆盖率为 0%；
   - app 内（前端为 `src/` 非测试文件）除定义外没有同名引用（名字统计）；
   - **全仓非测试文件里没有同名字面引用**（排除定义行，含字符串和注释；用 `git ls-files` 的已跟踪文本文件）。有字面引用的一律改为待定；
   - 不在保护项里，且不属于旧版清单。
   
   测试里有同名引用的，理由里写明「删时需同改测试」，标签不变。
3. **等 v11 切默认后删**：旧版清单（`legacy_memory.csv`）里标为旧版的。即使已是死代码，也按 U 的边界「旧版记忆等 v11 切默认后整块删」归此类。只测旧版的测试、只为旧版服务的脚本，本单没有逐个判定，归待定。
4. **要瘦身**：
   - 后端：所在文件的防御密度排在产品文件前 20%（阈值 27.47 次/千行，产品文件指 `backend/app` 与 `packaging/windows` 非测试，见 `summary.json`），或「重复校验 + 吞异常」合计 5 次及以上。候选行是含防御项的符号的行数。
   - 前端：所在文件的密度排前 20%（阈值 49.11 次/千行，产品文件指非测试的 `desktop/src` 与 `desktop/scripts` 下的 TS 文件），或不重新抛的 catch 合计 5 处及以上。候选行是含 catch 或 as any 的顶层语句的行数。
   - 「强候选」（派单第二节：vulture 报未用且覆盖率为 0）：后端 13 条，其中 11 条「现在就能删」、2 条「等 v11」。字面引用检查后没有条目被改为待定。
5. **锁死实现**：测试文件每千行 10 次及以上。候选行是整个测试文件。
6. **待定**：规则套不上或需要人工判断的：
   - vulture 报未用，但覆盖率非 0；
   - knip 报未用，但覆盖率非 0，或只有类型（类型没有运行时语句）；
   - knip 导出覆盖率 0%，但非测试文件里仍有同名引用（同文件内的调用也算）；
   - knip 文件名只在字符串里被引用（knip 只看 import）；
   - 旧版清单里标为待定的（只按文件名判断、或依据不足）；
   - CSS 类名字面搜不到（多为拼接类名，误报）；
   - `backend/scripts` 的每个文件一条（理由写覆盖率和「是否只为旧版服务未核」）。`desktop/src/dev-mock/status-preview.tsx` 因 knip 的文件问题归待定。

## 抽查（派单第 78 行的要求）

- 方法：从「现在就能删」的最终名单（15 条，按标签、类别、文件、起止行排序）里用 `random.Random(159).sample(…, 10)` 抽 10 条，结果在 `out/spotcheck_sample.csv`。
- 逐条核实：`rg -w` 在全仓搜名字（排除 `inventory/`，遵守 `.gitignore`），看是否只有定义处；覆盖率从 `coverage.json`（后端）或 `coverage-final.json`（前端）读语句命中；vulture 与 knip 的原始行对得上。
- 结果：最终 10 条全部核实，都是「只有定义处、覆盖率为 0、工具报未用」。报告里有逐条表格。
- **首轮抽查的教训**：第一次抽的 10 条里有 2 条是误报，分别是 `azimuthOf`（同文件第 117 行仍调用它，那段代码未执行，覆盖率为 0）和 `status-preview.tsx`（`vite-plugin-mock.ts` 第 92 行用字符串把它注入开发服务器的 HTML，`runtime.test.ts` 也引用了路径）。据此新增了「全仓非测试文件无同名字面引用」这条规则，并按同一个 seed 从修正后的名单重抽。首轮的 10 条和结论都保留在报告里。

## 验证

- 后端 pytest：3541 个用例，3530 passed、8 skipped、3 xfailed，1072.62 s，exit 0。8 条跳过中，7 条是 Windows 专用（`relies on USERPROFILE`、`junction aliasing requires Windows`），1 条因 IPv6 环回不可用。Linux 上跳过的不代表 Windows 通过。3 条 xfailed 是预期失败（`test_memory_v11_api.py` 的 S4、S6）。junit 在 `$RUN/out/junit-backend.xml`。
- 后端覆盖率：`--cov=app --cov=scripts`，83 个文件，语句覆盖 80.39%（16375/20369）。`coverage.json` 在 `$RUN/cov/`。
- 前端 vitest：76 个文件，883 passed、2 expected fail（885）。本次数字与基线记录的 819 不同，以本次运行为准。覆盖率 JSON 在 `$RUN/cov-fe/`。
- knip：退出码 1，36 个文件级问题条目（见上文），属正常。

## 已知误报和限制

- **名字统计**：后端引用数按名字计，同名不同对象合并，偏高。这只会让「现在就能删」变少，不会误删。
- **动态访问**：`getattr` 拼接名、框架注册、动态导入检测不到。字面引用检查覆盖字符串里的同名，但覆盖不了拼接出的名字。
- **字面检查范围**：只查 `git ls-files` 里的已跟踪文本文件（扩展名白名单，≤2 MB），`.gitignore` 里的文件不查。通用名（如 `PITCH`、`PAGE_SIZE`、`default`）的字面引用多，会让「待定」偏多（噪声），这是有意保守。
- **平台**：覆盖率只在 Linux 上测。Windows 专用测试跳过，覆盖率为 0 的代码可能只是平台原因，需要 U 在 Windows 上复核。
- **packaging/windows**：非测试 Python 纳入了防御密度、保护标注和符号表，但不跑 vulture，也不计覆盖率（pytest 只测 app 和 scripts），所以这里不产生「现在就能删」。
- **backend/scripts**：不跑 vulture（只扫 app），不产生死代码类候选，只按文件出「待定」。覆盖率见 `py_symbols.csv` 和 `py_zero_coverage.csv`。
- **Rust**：`desktop/src-tauri/src/*.rs` 只计行数，不做防御和死代码扫描（派单未要求）。
- **knip**：默认设置，对 Vite 入口、Tauri 和动态导入可能有误报。0 条未用依赖，只说明默认设置下没有发现。
- **防御密度**：只看次数，不看严重性。TS 的密度里 `?.` 和 `??` 占大头，排在前面的 TSX 不一定有过度防御。
- **CSS**：拼接类名（如 `nixie-*`、`presence-*`）字面搜不到，属于误报，共 32 条。
- **旧版清单**：人工整理（`legacy_memory.csv`），evidence 列写明依据。「读过代码」或「grep 核过调用方」的才标旧版；只按文件名判断的标为待定。

## 与派单不一致之处（已按下列处理）

- 分支名：「基线」节写 `claude/code-inventory-20261101`，「交付」节写 `codex/code-inventory-20261101`。按「基线」节。
- 路径：派单写 `memory_v11/jobs.py`，实际是 `backend/app/services/memory_v11/jobs.py`。行号一致（`memory_mode()` 在 1214–1215）。
- 后端覆盖率：派单要求 `--cov=app` 与 `--cov=scripts` 分两次跑。本次合在一次 pytest 里，再按文件拆分。逐文件数据与分两次跑相同。
- 前端覆盖率范围：派单没指定，取 `src/**/*.{ts,tsx}`。
- 测试归类：`desktop/src/test/` 和 `desktop/scripts/*.test.*` 归为测试（派单未列）。
- 提交身份：本机 git 已配置为 meditecnic 的 noreply 地址（与派单一致），提交时没有加 `-c user.*`，也没有改 git config。
- 执行方：派单指定 Factory Haiku 5.5。本单实际由当前会话执行（非 Haiku），在报告「谁做的」里写明。
