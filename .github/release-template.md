本版本只支持 **Windows x64**。

安装包未做 Authenticode 签名。若 Windows SmartScreen 提示未知发布者，请确认下载来自本仓库且校验和一致，再选择「更多信息 → 仍要运行」。更新包的 Tauri 签名用于验证更新来源，不代替 Windows 代码签名。

随包模型含 CC BY-NC 4.0 许可，因此整体仅供非商业使用。第三方许可随安装包提供；语音组件对应源码见同版本的 `amadeus-<版本>-speech-sources.zip`。

下载 `SHA256SUMS.txt` 和安装包，在 PowerShell 中运行 `Get-FileHash .\Amadeus_<版本>_x64-setup.exe -Algorithm SHA256`，将 Hash 与清单中对应文件的 SHA-256 比较，忽略大小写；必须完全一致后再安装。其他附件可用同一命令校验。
