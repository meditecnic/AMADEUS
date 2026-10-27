# Windows 冻结后端

构建目标为 Windows x64、CPython 3.11、PyInstaller onedir。默认生成
`desktop/src-tauri/resources/backend/amadeus-backend.exe`，供 Tauri resources 使用。
所有命令从仓库根执行。构建目录应放在空间充足的磁盘上，产物不提交。

## 构建

先准备 `intfloat/multilingual-e5-small` 的完整本地 snapshot，固定 revision 为
`614241f622f53c4eeff9890bdc4f31cfecc418b3`。来源目录只读，脚本按白名单复制文件，
`E5_FILES` 固定十个文件的期望 SHA-256，复制前核对来源，复制后再次核对产物。
任一哈希不符即停止；不下载模型，不复制其他模型或用户缓存。

PowerShell 示例（将三个路径替换为本机路径）：

```powershell
$buildPython = 'X:/Python311/python.exe'
$buildWork = 'X:/build-artifacts/backend-build'
$e5Snapshot = 'X:/models/models--intfloat--multilingual-e5-small/snapshots/614241f622f53c4eeff9890bdc4f31cfecc418b3'
& $buildPython packaging/windows/build_backend.py --python $buildPython --work-dir $buildWork --e5-source $e5Snapshot
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
sklearn 按完整路径预加载的运行库保留包内副本及公共副本；本次构建的
`msvcp140.dll`、`VCOMP140.DLL` 两组副本哈希均不同。
根目录的这两份库来自构建机已安装的 VC++ 运行库，版本随构建机变化；
`vcruntime140*.dll` 也可能来自基础 Python 安装目录。具体来源、版本以本次清单为准，
固定分发来源由后续发布工程完成。
排除 `torch/include/`、sherpa 的 `include/` 和 `.lib`；仍分发 sklearn、transformers
自带的头文件及 sklearn 导入库。sherpa 的 c-api.dll、cxx-api.dll 保留。
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
使用 netstat 同时核对后端 PID 的 IPv4/IPv6 TCP 监听，要求只有 `127.0.0.1`。
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
逐项记录基础 Python 和 Windows 来源二进制的包内路径、来源、文件版本及 SHA-256。
没有 PE 文件版本资源时，`file_version` 为 null。此清单供后续运行库及许可分发使用。

`THIRD_PARTY_LICENSES/manifest.json` 通过 TOC 来源文件和各发行包的 RECORD 对应，
只列实际收集到的发行包，不扫描后直接宣称整个 venv 都被分发。每条记录包含名称、
版本、License / License-Expression / License-File 字段、许可文件路径和 SHA-256；
许可文件按发行包放入 ZIP。`without_license_files` 显式列出没有 dist-info 许可文件的包。
模型卡随模型保存，`e5-model.json` 记录模型来源、revision 和文件哈希。

## 交付边界

- 验收覆盖本机冻结产物、离线 E5、sherpa 导入、假服务聊天和退出；ASR/VAD 推理、
  真实 Provider、TTS、素材包、无开发环境的 Windows VM、安装/升级/卸载需另外验收。
- 当前目录不生成 NSIS，不安装系统软件。VC++ / WebView2 前置检查、eSpeak GPL
  原文和对应源码获取方式、前端/Rust/native 许可补全及最终许可入安装包由 P3 完成。
- 本地令牌和跨前后端的动态端口发现属于 P4；本入口的端口环境变量不代替该协议。
- 未签名。最终发布应以待发布产品版本重新构建并重新验收。
