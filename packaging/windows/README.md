# Windows 冻结后端与安装包

构建目标为 Windows x64、CPython 3.11、PyInstaller onedir。默认生成
`desktop/src-tauri/resources/backend/amadeus-backend.exe`，供 Tauri resources 使用。
所有命令从仓库根执行。构建目录应放在空间充足的磁盘上，产物不提交。

## 构建

先准备 `intfloat/multilingual-e5-small` 的完整本地 snapshot，固定 revision 为
`614241f622f53c4eeff9890bdc4f31cfecc418b3`。来源目录只读，脚本按白名单复制文件，
`E5_FILES` 固定十个文件的期望 SHA-256，复制前核对来源，复制后再次核对产物。
任一哈希不符即停止；不下载模型，不复制其他模型或用户缓存。
哈希已经一致的暂存模型文件直接复用，并继续核对来源及产物。

PowerShell 示例（将路径替换为本机路径）：

```powershell
$buildPython = 'X:/Python311/python.exe'
$buildWork = 'X:/build-artifacts/backend-build'
$e5Snapshot = 'X:/models/models--intfloat--multilingual-e5-small/snapshots/614241f622f53c4eeff9890bdc4f31cfecc418b3'
$vcRedist = 'X:/BuildTools/VC/Redist/MSVC/14.51.36231/x64'
& $buildPython packaging/windows/build_backend.py --python $buildPython --work-dir $buildWork --e5-source $e5Snapshot --vc-redist $vcRedist
```

`--python` 只用于建立专用 venv，默认是调用脚本的 Python。
`--venv` 默认为 `WORK_DIR/venv`，必须位于工作目录内；不使用系统 site-packages。
`--output` 可覆盖默认输出目录，目标必须不存在，且不能与工作目录互相包含。
`--prepare-only` 只准备依赖，便于提前下载。`--clean` 清除 PyInstaller 分析缓存。
venv、pip 缓存、临时目录、
PyInstaller 缓存、日志和中间产物都写入 `--work-dir`。
构建子进程的 PATH 只包含构建 venv 的 Scripts、基础 Python 安装目录、
Windows System32 和 Windows 目录；实际 PATH 写入 `build-config.json`。
每个 BINARY/EXTENSION 的来源必须位于构建 venv、基础 Python 的 `sys.base_prefix`
或 Windows 目录内，否则在收集产物前报错，列出包内路径和来源。

依赖来源：

- `backend/requirements.txt` 定义后端运行时需求。
- `backend/requirements-dev.lock` 提供版本、平台条件和哈希。构建器生成
  `WORK_DIR/requirements-runtime.lock`，保留这些信息，排除 `pytest`、
  `pytest-asyncio`、`pytest-timeout`、`ruff`、`iniconfig`、`pluggy`。
  `rich` 依赖的 `pygments` 仍是运行时依赖。升级仓库依赖时应复核此排除表。
- `requirements-build.txt` 固定构建工具和 Windows wheel 的 SHA-256。

安装全部使用 `--only-binary=:all: --require-hashes`，并运行 `pip check`。
sherpa-onnx 和 sherpa-onnx-core 均使用仓库锁定的官方 1.13.8 wheel。
不从 sdist 编译，也不装测试工具。Windows torch wheel 的版本沿用仓库锁。

`backend.spec` 和 `bundle_support.py` 收集原生库、包数据和延迟导入模块。
应用数据白名单在 `build_backend.py:APP_FILES`：两份角色 JSON、persona bible、
模型能力清单、发音词典和 few-shot JSON；不整棵复制 backend 或前端资源。
E5 使用包内 Hugging Face hub 缓存布局及 `refs/main`，入口强制离线，
不复制到用户目录。tokenizer 和模型文件的白名单在 `E5_FILES`。

重复 DLL 仅处理根目录副本与明确的包内库目录副本：逐对核对 SHA-256，
相同才移除根目录副本；不同则都保留。清单在 `dll-deduplication.json`。
最终目录中的同名 DLL 另行逐份计算哈希，写入 `duplicate-dll-audit.json`。
sklearn 按完整路径预加载的运行库保留包内副本及公共副本，即使哈希相同也不移除；
各副本的文件版本与 SHA-256 记录在库存中。
`--vc-redist` 必填，指定 Build Tools 的 `Redist/MSVC/<版本>/x64`。
其下必须恰好有一个 `Microsoft.VC14*.CRT` 和一个 `Microsoft.VC14*.OpenMP`，
实际目录名及文件版本写入清单。当前锁定 wheel 需要至少 14.51 的运行库；
VS 2026 使用的目录名为 `Microsoft.VC145.*`。
冻结后把根目录和包内私有 VC 运行库全部替换为所选 Redist 文件；私有副本的路径及
文件名保持，包括 delvewheel 的哈希后缀，匹配来源时去掉该后缀。库存记录替换前
版本、哈希及替换后哈希。门禁认可导入方目录、`_internal/` 根目录，以及导入方所属包
的 `<package>.libs`、`<package>/.libs` 加载目录；其他包的同名 DLL 不满足该导入。
这些位置均缺少时，从 Redist 补到根目录。NumPy 已有私有副本时不再额外补根目录别名。
`additions` 列出补入文件及导入方。保留构建 PATH 顺序以供 PyInstaller 查找基础 Python
依赖，最终全部 VC 副本由显式替换和 SHA-256 校验确定。
所有 PE 文件都核对普通和延迟导入表；导入 VC 运行库的组件，其链接器版本不得高于
所选 Redist 的主、次版本。每个导入必须能在上述加载目录找到，并核对同名
私有预加载候选副本的版本；组件链接器版本不得高于任何候选副本。缺文件、版本
超出或副本不匹配 Redist 时停止，详情见 `vc-runtime-audit.json`。`audit_bundle` 是
只读门禁，便于对已生成的冻结包及故意改坏的副本进行核对。
`MSVCP140_ATOMIC_WAIT.dll` 是自检模块快照中观察到的运行时加载项，未出现在 PE
导入表中；因此独立要求根目录必须包含它，冻结时缺少就从 Redist 补齐，门禁缺少
就报错。库存单列 `required_runtime_loads` 和 `missing_runtime_loads`，不推断加载者。
后端不使用 pythonwin GUI/MFC 接口，冻结时排除 `pythonwin`、`win32ui`、`win32uiole`，
避免分发依赖未入包 MFC 的未使用扩展；其余 pywin32 功能仍按分析结果收集。
排除 `torch/include/`、sherpa 的 `include/` 和 `.lib`；仍分发 sklearn、transformers
自带的头文件及 sklearn 导入库。锁定的 sherpa 1.13.8 排除
`sherpa-onnx-c-api.dll`、`sherpa-onnx-cxx-api.dll`：包内其他 PE 不导入它们，
Python 源码也不在运行时加载它们；`_info.py` 仅为命令行输出编译参数。
两个 DLL 之间的导入不计为外部引用。这样少分发一份静态编入的 eSpeak NG；
`_sherpa_onnx.pyd` 中的 eSpeak NG 仍分发 GPLv3 原文及源码说明。
每次构建重新核对普通/延迟导入及 Python 加载调用，记录排除前后的文件数、总字节
和源码哈希到 `sherpa-imports.json`；存在外部引用或加载调用时保留两个 DLL。
dist-info 下的许可树存入经过字节校验的 ZIP，避免深路径。

## 验收

先运行自检（使用新的临时数据目录，凭据后端设为 memory，禁用 dotenv）：

```powershell
$env:AMADEUS_DATA_DIR = 'X:/build-artifacts/check-data'
$env:AMADEUS_CREDENTIAL_BACKEND = 'memory'
$env:PYTHON_DOTENV_DISABLED = '1'
& desktop/src-tauri/resources/backend/amadeus-backend.exe --self-check
```

自检导入 app，离线加载 E5 并编码中文句子，检查 384 维、有限数值和单位范数，
导入 sherpa 原生扩展，还会实际加载 sqlite_vec 扩展。失败返回非零退出码。
不下载或运行 ASR 模型。

完整冻结验收使用构建 venv 里的 websockets；后端子进程只继承 Windows 系统
变量及脚本生成的隔离变量，其 PATH 只有 Windows 和 System32，工作目录位于
新建的临时目录，清除 Python、HF 缓存、真实凭据和 sidecar 配置的继承影响。

```powershell
& "$buildWork/venv/Scripts/python.exe" packaging/windows/smoke_frozen.py --exe desktop/src-tauri/resources/backend/amadeus-backend.exe --work-dir 'X:/build-artifacts/backend-smoke'
& "$buildWork/venv/Scripts/python.exe" packaging/windows/smoke_frozen.py --exe desktop/src-tauri/resources/backend/amadeus-backend.exe --work-dir 'X:/build-artifacts/backend-smoke' --memory-mode v11
```

验收自带 loopback 假 OpenAI 兼容服务，禁用 TTS，使用空闲的非 8000 端口；
使用 Windows `GetExtendedTcpTable` 同时核对后端 PID 的 IPv4/IPv6 TCP 监听，
要求只有 `127.0.0.1`。
默认不传 `AMADEUS_MEMORY_MODE`，覆盖产品的 legacy 默认模式；另一轮显式启用 v11。
检查 `/health`、WebSocket 的 auth → session.ready → chat.send → turn.completed、
用户及助手历史落库。分别杀掉假父进程和关闭 stdin，要求后端 10 秒内正常退出，
检查进程残留、SQLite 排他事务以及数据库文件重命名。
结果、事件、后端日志和数据库保留在打印的 `SMOKE_ROOT` 下。

后端启动会探测固定的 9880 端口。因此验收先独占该端口并返回空响应；若已有服务
占用则验收报错退出，应在该端口空闲的环境中重跑。脚本不调用
`/health/dependencies`，不连接真实模型服务或 TTS，不读写 Windows 凭据管理器。

入口只监听 `127.0.0.1`；`BACKEND_PORT` 默认 8000，可覆盖用于独立验收。
`AMADEUS_PARENT_PID` 设置后看护对应进程，stdin EOF 也会请求 uvicorn 正常关闭。
桌面宿主需要在后端存活期间保持 stdin 管道打开。stdout/stderr 使用 UTF-8。
入口清除测试兼容变量 `AMADEUS_DB_PATH`，使数据库继续受 `AMADEUS_DATA_DIR` 管理；
语音、素材相关用户配置（`SOVITS_SIDECAR_COMMAND`、`AMADEUS_ASSET_PACK_DIR`、
`SENSEVOICE_*`、`AMADEUS_VAD_MODEL`、`AMADEUS_REF_*`）继续继承。
uvicorn 最多等待活动请求 3 秒，为桌面宿主的 5 秒退出期限留出清理时间。
stdin 不是管道时，stderr 会记录等待 EOF 和请求退出的原因。

## 构建证据和许可

`WORK_DIR/logs/` 保存 venv、pip、PyInstaller、清单生成日志。
`build-config.json` 保存依赖输入哈希和模型文件哈希；`analysis.json` 保存 PyInstaller
TOC 的来源路径；`bundle-inventory.json` 保存版本、体积、最大文件/目录、去重和许可统计；
`build-result.json` 保存构建耗时。路径字段含本机构建位置，保持为本地产物。
`bundle-inventory.json` 的 `allowed_roots` 记录二进制来源范围；`non_venv_binaries`
逐项记录基础 Python、Windows 和所选 Redist 来源二进制的包内路径、来源、文件版本及 SHA-256。
没有 PE 文件版本资源时，`file_version` 为 null。此清单用于运行库来源及许可核对。

`THIRD_PARTY_LICENSES/manifest.json` 通过 TOC 来源文件和各发行包的 RECORD 对应，
只列实际收集到的发行包，不扫描后直接宣称整个 venv 都被分发。每条记录包含名称、
版本、License / License-Expression / License-File 字段、许可文件路径和 SHA-256；
许可文件按发行包放入 ZIP。`without_license_files` 显式列出没有 dist-info 许可文件的包。
模型卡随模型保存，`e5-model.json` 记录模型来源、revision 和文件哈希。

## NSIS 安装包

先安装 VS 2026 Build Tools 的「使用 C++ 的桌面开发」工作负载。构建器通过
`vswhere` 要求唯一的完整 VS 2026 实例，选择其最高版本的 Redist，核对文件版本
至少为 14.51。`--vc-redist` 必须指向这个 x64 目录。Node、Rust 和 Python 3.11 x64
需已可用；工具版本随产物保存。

```powershell
py -3.11 packaging/windows/build_installer.py --work-dir 'X:/build-artifacts/installer' --e5-source $e5Snapshot --vc-redist $vcRedist --config packaging/windows/p3-test.conf.json
```

一条命令依次冻结后端、生成统一许可目录、执行完整 `npm ci`、运行 `tauri build`。
生成许可目录时先在工作目录内安装非 dev npm 依赖，供许可收集使用；随后在
`desktop/` 执行完整依赖安装供前端构建使用。所有 npm 缓存、venv、临时文件、
`CARGO_TARGET_DIR`、前端产物和安装包均位于 `--work-dir` 下；`desktop/node_modules`
是工作区内的构建依赖。

每次构建创建独立的 `WORK_DIR/runs/<UTC时间>/`，保存后端、统一许可目录、前端产物、
实际传给 Tauri 的 JSON 配置、各阶段日志、冻结证据及安装包。
`WORK_DIR/installer-result.json` 指向最新成功结果，记录各阶段耗时、工具及链接器路径、
主程序导入表和链接器版本、安装包大小与 SHA-256。缓存和 Cargo 输出可在同一工作目录复用。
构建器显式选择该 VS 2026 的 `link.exe`，并启用 `STATIC_VCRUNTIME`；最终仍以主程序
实际 PE 导入表没有动态 VC 运行库导入为验收条件。实际编译阶段透传 `-- --locked`。

两个构建脚本共用 `bundle_support.sanitized_build_environment`，只继承白名单中的
Windows 系统/用户目录、PATH/PATHEXT、Cargo/Rustup 目录与工具链选择、标准代理和
CA 信任路径；具体名字见 `BUILD_ENV_NAMES`。未知变量一律删除，包括任意命名的
API 密钥、访问令牌和应用配置；只打印被删除的变量名。安装构建器保留 PATH，供
npm、node、cargo 查找工具；HTTP_PROXY、HTTPS_PROXY、ALL_PROXY、NO_PROXY 等代理
变量及其大小写形式保留，供 npm、cargo 和源码下载联网。代理 URL 如果含用户名和
口令，也会随这个允许的代理设置传给构建子进程。后端冻结继续使用只含专用 Python
与 Windows 目录的 PATH；这些处理只作用于子进程，不修改用户的持久环境变量。

Tauri CLI 2.11.3 在 Rust interface 初始化时调用 `rewrite_manifest` 同步 tauri 和
tauri-build 的 features，并重新序列化 Cargo.toml；CRLF 可能变为 LF。副本对照实验中，
是否传 version 覆盖都会发生相同行尾变化。允许修改范围内没有简单的 CLI 开关避开
这一步；构建结束打印 `CARGO_MANIFEST_NOTE`，记录字节变化及规范化后内容是否相同。
脚本不恢复清单、不运行 git checkout；内容若发生实质变化则报错。

`p3-test.conf.json` 使用 `com.amadeus.desktop.p3test`、`Amadeus P3 Test` 和 `0.1.0`，
仅通过 `tauri build --config` 覆盖测试标识，不改正式配置。测试数据默认位于
`%LOCALAPPDATA%/com.amadeus.desktop.p3test/data/`。不传 `--config` 时使用正式标识；
安装验收应始终传测试配置。安装模式为 `currentUser`，WebView2 保持在线
`embedBootstrapper`。卸载的「删除应用数据」默认不勾，静默卸载保留用户数据。

`licenses/manifest.json` 统一记录 Python、CPython/native、VC、Windows 目标的 Rust
运行时依赖、非 dev npm 包、字体、E5 和 GPL/LGPL 材料。Rust 图不遍历 build/dev
依赖和 proc-macro 依赖链；许可原文来自本机 crate 缓存。Python 许可 ZIP 沿用冻结清单，
缺许可 wheel、crate 和 npm 包的上游原文由 `upstream_materials.json` 固定 URL、ref
和 SHA-256。crate 提交与 `.cargo_vcs_info.json` 核对；npm 提交按 registry gitHead
确认。selectors 按源码头声明附官方 MPL-2.0，机器字段 `text_source=generic`、
`version_correspondence=null`，并列入 coverage_gaps；这份通用文本未宣称来自对应
提交。react-remove-scroll-bar 2.3.8 的对应
提交及 tag 无法取得，使用固定的其他提交原文，并在清单明确记录版本对应例外。
libquadmath 未在二进制中找到符号或字符串，LGPL 原文作为预防保留。
没有许可文件、版本差异、CPython 主许可未覆盖的段落及未确认的源码信息均单列。
许可 ZIP 成员时间戳固定为 1980-01-01，避免原始文件 mtime 影响容器哈希。安装后的
核对命令检查所有许可文件、ZIP 成员哈希及组件安装路径，并反查目录文件集合，
缺失或多余文件均拒绝；直接核对构建许可目录可调用 `verify(root, license_dir)`：

```powershell
& 'X:/build-artifacts/installer/backend-build/venv/Scripts/python.exe' packaging/windows/license_bundle.py --verify 'X:/安装 测试'
```

eSpeak NG、piper-phonemize 的归档地址和预期 SHA-256 从 sherpa v1.13.8 的固定 CMake
文件读取。源码包保留这些归档、sherpa v1.13.8 源码及许可说明，不解包或执行下载的代码。
Sherpa v1.13.8 归档固定 SHA-256 为
`b63b7613812346f2d1396a3a7f94accd47539e3dadc3ea385ba6474c70ab9897`。
下载必须提供预期哈希；源码归档上限 64 MiB，许可材料上限 2 MiB，同时检查响应
长度与实际读取量。先写临时文件，核对通过后改名；失败清理临时文件，损坏缓存
会被删除并报错，下次调用可重新下载。
可独立运行以下命令生成源码包；发布者将它与对应版本的安装包一起发布：

```powershell
py -3.11 packaging/windows/source_materials.py --work-dir 'X:/build-artifacts/installer/materials' --version 0.1.0
```

统一许可目录和源码包均为生成物，不提交。源码目录默认资源占位为
`desktop/src-tauri/resources/licenses/`；构建器通过 JSON Merge Patch 将后端和许可
资源映射到工作目录中的实际生成物。

## 交付边界

- 冻结验收覆盖本机产物、离线 E5、sherpa 导入、假服务聊天和退出。安装包需要另外完成
  安装、覆盖安装、卸载及数据保留验收；ASR/VAD 推理、真实 Provider、TTS、素材包、
  无开发环境的 Windows VM、从旧版本升级和正式标识安装不在本机测试范围内。
- 构建器不安装系统软件。VC 运行库以 app-local 文件分发，安全更新依赖重新构建发版；
  干净环境中的 VC / WebView2 验收和 CI 出包由后续发布验收完成。
- 本地令牌和跨前后端的动态端口发现属于 P4；本入口的端口环境变量不代替该协议。
- 未签名。最终发布应以待发布产品版本重新构建并重新验收。
