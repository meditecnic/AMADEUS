"""Install, log in, close and uninstall on a disposable, elevated Windows runner."""
from __future__ import annotations

import argparse
import base64
import ctypes
from ctypes import wintypes
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit

from smoke_frozen import fake_service, process_snapshot, update_children, verify_listeners, write_json
from bundle_support import digest, sanitized_build_environment

IDENTIFIER = "com.amadeus.desktop"
AUDIT_GUID = "{0CCE9226-69AE-11D9-BED3-505054503030}"  # Filtering Platform Connection


def require(condition: object, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def safe_text(value: str) -> str:
    # Evidence never includes request headers/bodies, IPC values, or full CDP frames.
    return re.sub(r"[A-Za-z0-9_-]{32,}", "[redacted]", value)


def safe_url(value: str) -> str:
    parts = urlsplit(value)
    return f"{parts.scheme}://{parts.hostname or ''}{parts.path}"


def is_update_url(value: str) -> bool:
    parts = urlsplit(value)
    return (parts.hostname == "github.com" and
            parts.path.lower().startswith("/meditecnic/amadeus/releases/"))


def check_clean(local: Path) -> Path:
    root = local / IDENTIFIER
    require(not root.exists(), f"Refusing existing user data: {root}; use a clean disposable runner")
    return root / "data"


def powershell(script: str) -> object:
    output = subprocess.check_output(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); $ErrorActionPreference='Stop'; " + script],
        text=True, encoding="utf-8", timeout=30,
    )
    return json.loads(output.lstrip("\ufeff"))


def registration() -> dict | None:
    import winreg

    key_name = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    try:
        parent_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_name)
    except FileNotFoundError:
        return None  # A clean profile may not have the Uninstall key until something installs.
    with parent_key as parent:
        for index in range(winreg.QueryInfoKey(parent)[0]):
            with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as key:
                try:
                    if winreg.QueryValueEx(key, "DisplayName")[0] == "Amadeus":
                        # Tauri's NSIS template stores this value with enclosing quotes.
                        return {"location": winreg.QueryValueEx(key, "InstallLocation")[0].strip('"')}
                except FileNotFoundError:
                    continue
    return None


def shortcuts(exe: Path) -> list[Path]:
    # Resolve targets, rather than assuming the Windows UI language or shortcut folder layout.
    target = str(exe).replace("'", "''")
    result = powershell(
        "$ws=New-Object -ComObject WScript.Shell; "
        "$base=Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs'; "
        f"ConvertTo-Json -Compress -InputObject @(Get-ChildItem $base -Recurse -Filter *.lnk | Where-Object {{ "
        f"$ws.CreateShortcut($_.FullName).TargetPath -eq '{target}' }} | "
        "ForEach-Object { $_.FullName })"
    )
    return [Path(path) for path in result]


def command_step(command: list[str], label: str, root: Path, env: dict) -> dict:
    started = time.monotonic()
    with (root / f"{label}.log").open("wb") as stream:
        done = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT, timeout=300)
    result = {"exit_code": done.returncode, "seconds": round(time.monotonic() - started, 3)}
    print(label.upper() + " " + json.dumps(result), flush=True)
    require(done.returncode == 0, f"{label} exited {done.returncode}; see {label}.log")
    return result


def close_windows(pid: int) -> int:
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    count = 0
    failed = False

    @callback
    def visit(window, _):
        nonlocal count, failed
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(window):
            failed |= not bool(user.PostMessageW(window, 0x0010, 0, 0))
            count += 1
        return True

    user.EnumWindows(visit, 0)
    require(not failed, "WM_CLOSE could not be posted")
    return count


def wait_until(check, timeout: float, message: str, tick=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if tick:
            tick()
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise RuntimeError(message)


class CDP:
    def __init__(self, url: str):
        import websocket

        self.ws = websocket.create_connection(url, timeout=5, suppress_origin=True,
                                              http_no_proxy=["127.0.0.1", "localhost"])
        self.sequence = 0
        self.requests: list[dict] = []
        self.errors: list[dict] = []
        self.allowed: list[dict] = []
        self.sent_types: list[str] = []

    def event(self, event: dict) -> None:
        method, params = event.get("method"), event.get("params", {})
        if method == "Network.requestWillBeSent":
            request = params["request"]
            self.requests.append({"method": request["method"], "url": safe_url(request["url"])})
        elif method == "Network.webSocketFrameSent":
            try:
                self.sent_types.append(json.loads(params["response"]["payloadData"]).get("type", ""))
            except (ValueError, AttributeError):
                pass
        elif method == "Log.entryAdded" and params["entry"]["level"] == "error":
            entry = params["entry"]
            item = {"source": entry.get("source"), "url": safe_url(entry.get("url", "")),
                    "text": safe_text(entry["text"])}
            # Optional asset pack is absent on clean installs: assetPack.ts; ops report [181] III.2.
            if (entry.get("source") == "network" and "404" in entry["text"] and
                    urlsplit(entry.get("url", "")).path == "/api/asset-pack/manifest.json"):
                self.allowed.append(item)
            else:
                self.errors.append(item)
        elif method == "Runtime.exceptionThrown":
            self.errors.append({"source": method, "text": safe_text(params["exceptionDetails"]["text"])})
        elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
            self.errors.append({"source": method, "text": safe_text(" ".join(
                str(item.get("value", item.get("description", "object"))) for item in params["args"]))})

    def call(self, method: str, params: dict | None = None) -> dict:
        self.sequence += 1
        self.ws.send(json.dumps({"id": self.sequence, "method": method, "params": params or {}}))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            message = json.loads(self.ws.recv())
            if message.get("id") == self.sequence:
                require("error" not in message, f"CDP {method} failed")
                return message.get("result", {})
            self.event(message)
        raise RuntimeError(f"CDP {method} timed out")

    def evaluate(self, expression: str):
        result = self.call("Runtime.evaluate", {"expression": expression, "returnByValue": True})
        require("exceptionDetails" not in result, "DOM evaluation failed")
        return result["result"].get("value")

    def screenshot(self, path: Path) -> None:
        path.write_bytes(base64.b64decode(self.call("Page.captureScreenshot", {"format": "png"})["data"]))


def connect_cdp(port: int, timeout: float) -> CDP:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def attempt():
        try:
            with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=0.5) as response:
                pages = [item for item in json.load(response) if item.get("type") == "page"]
            if pages:
                return CDP(pages[0]["webSocketDebuggerUrl"])
        except (OSError, ValueError):
            return None

    return wait_until(attempt, timeout, "WebView2 CDP unavailable: check interactive desktop, runtime and debug port")


def audit_start() -> int:
    # Also observes native Rust updater traffic, which cannot appear in renderer CDP events.
    subprocess.run(["auditpol.exe", "/set", f"/subcategory:{AUDIT_GUID}", "/success:enable", "/failure:enable"],
                   check=True, stdout=subprocess.DEVNULL, timeout=15)
    return powershell("(Get-WinEvent -LogName Security -MaxEvents 1).RecordId | ConvertTo-Json")


def audit_finish(bookmark: int, install: Path, calibration_port: int, evidence: Path) -> list[dict]:
    rows = powershell(
        "$filter=\"*[System[(EventID=5156 or EventID=5157) and EventRecordID > " + str(bookmark) + "]]\"; "
        "ConvertTo-Json -Compress -InputObject @(Get-WinEvent -LogName Security -FilterXPath $filter | ForEach-Object { "
        "$xml=[xml]$_.ToXml(); $row=@{}; foreach($d in $xml.Event.EventData.Data){$row[$d.Name]=$d.'#text'}; "
        "$row })"
    )
    require(any(int(row["ProcessID"], 0) == os.getpid() and int(row["DestPort"]) == calibration_port
                for row in rows), "Windows network audit has no calibration event; evidence is incomplete")
    # Event application paths use NT device names, so compare their installation path suffix.
    suffix = str(install).replace("/", "\\")[2:].casefold() + "\\"
    native = [{key: row[key] for key in ("Application", "DestAddress", "DestPort", "Direction")}
              for row in rows if suffix in row["Application"].casefold()]
    write_json(evidence, native)
    require(not any(row["Direction"] == "%%14593" and not ipaddress.ip_address(row["DestAddress"]).is_loopback
                    for row in native), "Installed native process attempted external network access (possible updater)")
    return native


def backend_pids() -> set[int]:
    return {pid for pid, (_, name) in process_snapshot().items() if name.lower() == "amadeus-backend.exe"}


def exercise(installer: Path, root: Path, timeout: float, result: dict, provider_port: int) -> None:
    data = check_clean(Path(os.environ["LOCALAPPDATA"]))
    print("INSTALL_STEP clean user-data root", flush=True)
    require(registration() is None and not backend_pids(), "Existing installation/backend; use a clean runner")
    env = sanitized_build_environment()  # Removes inherited AMADEUS_*, signing keys and provider credentials.
    env.update({"AMADEUS_CUSTOM_PROVIDER_BASE_URL": f"http://127.0.0.1:{provider_port}/v1",
                "AMADEUS_CUSTOM_PROVIDER_MODEL": "frozen-smoke", "AMADEUS_CREDENTIAL_BACKEND": "memory"})
    result["install"] = command_step([str(installer), "/S"], "install", root, env)
    entry = registration()
    require(entry is not None, "HKCU uninstall registration missing after install")
    install = Path(entry["location"])
    exe = install / "desktop.exe"
    links = shortcuts(exe)
    require(install.is_dir() and exe.is_file() and links, "Missing installation directory, desktop.exe or Start Menu shortcut")
    result["installation"] = {"directory": str(install), "shortcuts": [str(p) for p in links]}
    print("INSTALL_STEP HKCU registration, installation and Start Menu shortcut present", flush=True)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    env.update({"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": f"--remote-debugging-port={port}",
                "WEBVIEW2_WAIT_FOR_SCRIPT_DEBUGGER": "1"})
    bookmark = audit_start()  # Requires administrator rights on the disposable hosted runner.
    with socket.socket() as calibration:
        calibration.bind(("127.0.0.1", 0))
        calibration.listen()
        calibration_port = calibration.getsockname()[1]
        with socket.create_connection(("127.0.0.1", calibration_port)), calibration.accept()[0]:
            pass
    tracked: set[int] = set()
    launches = []
    cdp = None
    try:
        with (root / "desktop.log").open("wb") as log:
            started = time.monotonic()
            primary = subprocess.Popen([str(exe)], env=env, stdout=log, stderr=subprocess.STDOUT)
            launches.append(primary)
            tick = lambda: [update_children(p.pid, tracked) for p in launches]
            cdp = connect_cdp(port, timeout)
            for domain in ("Network", "Log", "Runtime", "Page"):
                cdp.call(domain + ".enable")
            # WebView2 pauses before the first script, so initial login requests/errors are observed.
            cdp.call("Runtime.runIfWaitingForDebugger")
            wait_until(lambda: cdp.evaluate("document.querySelector('#salieri-user')?.value === 'Salieri' && "
                       "document.querySelector('button[type=submit]')?.disabled === false"), timeout,
                       "Login page not ready: check backend logs and WebView2 runtime", tick)
            result["login_page_seconds"] = round(time.monotonic() - started, 3)
            cdp.screenshot(root / "login.png")
            print("INSTALL_STEP login page captured", flush=True)
            cdp.evaluate("document.querySelector('#salieri-pwd').focus()")
            cdp.call("Input.insertText", {"text": "Gott w\u00fcrfelt nicht"})
            cdp.evaluate("document.querySelector('button[type=submit]').click()")
            wait_until(lambda: cdp.evaluate("!document.querySelector('.salieri-login-form') && "
                       "document.querySelector('button[aria-label=\"模型供应商\"]')?.disabled === false"),
                       timeout, "Login did not reveal the workstation provider selector", tick)
            cdp.evaluate("document.querySelector('button[aria-label=\"模型供应商\"]').click()")
            option = "[...document.querySelectorAll('[role=option]')].find(e => e.textContent.trim() === 'OpenAI-compatible')"
            wait_until(lambda: cdp.evaluate(f"Boolean({option})"), 10, "Local keyless provider option missing", tick)
            cdp.evaluate(f"({option}).click()")
            wait_until(lambda: cdp.evaluate("document.querySelector('.ws2-online')?.textContent.trim() === 'ONLINE' && "
                       "!document.querySelector('.salieri-login-form')"), timeout,
                       "Real form login did not reach ONLINE", tick)
            cdp.screenshot(root / "online.png")
            print("INSTALL_STEP real login and keyless local provider reached ONLINE", flush=True)
            require(data.is_dir(), "Default LOCALAPPDATA data directory was not created")
            backends = backend_pids()
            require(len(backends) == 1 and backends <= tracked, "Expected one installed backend child")
            stdout = (data / "logs/backend-stdout.log").read_text(encoding="utf-8")
            ready = [json.loads(line.split(" ", 1)[1]) for line in stdout.splitlines()
                     if line.startswith("AMADEUS_BACKEND_READY ")]
            require(len(ready) == 1 and ready[0]["pid"] in backends, "Installed backend READY missing or wrong PID")
            result["listeners"] = verify_listeners(ready[0]["pid"], ready[0]["port"])
            second = subprocess.Popen([str(exe)], env=env, stdout=log, stderr=subprocess.STDOUT)
            launches.append(second)
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                tick()
                require(backend_pids() == backends, "Second launch created another backend or stopped the first")
                cdp.evaluate("true")  # Pump CDP network/console events during the observation window.
                time.sleep(0.05)
            result["second_launch_backend_count"] = len(backend_pids())
            print("INSTALL_STEP second launch kept one backend", flush=True)
            close_windows(second.pid)  # Dismiss the expected already-running modal.
            result["second_launch_exit_code"] = second.wait(timeout=10)
            cdp.evaluate("true")
            require(not cdp.errors, f"Console/runtime errors: {json.dumps(cdp.errors, ensure_ascii=True)}")
            require(not any(is_update_url(item["url"]) for item in cdp.requests), "Startup contacted updater URL")
            require(not ({"chat", "chat.send", "voice.start"} & set(cdp.sent_types)), "Unexpected chat/voice request")
            result["console_errors"] = 0
            started = time.monotonic()
            require(close_windows(primary.pid), "No visible primary window; interactive desktop unavailable")
            wait_until(lambda: not ((tracked | {p.pid for p in launches}) & process_snapshot().keys()), 10,
                       "WM_CLOSE left desktop/backend/WebView2 descendants after 10 seconds", tick)
            result["close_seconds"] = round(time.monotonic() - started, 3)
            result["desktop_exit_code"] = primary.wait(timeout=1)
            require(primary.returncode == 0, f"Desktop exited {primary.returncode} after WM_CLOSE")
            result["residual_processes"] = []
            print("INSTALL_STEP WM_CLOSE cleared tracked process tree", flush=True)
        result["native_connections"] = audit_finish(bookmark, install, calibration_port, root / "native-network.json")
        result["native_audit_calibrated"] = True
    finally:
        if cdp:
            write_json(root / "cdp-network.json", cdp.requests)
            write_json(root / "console.json", {"errors": cdp.errors, "allowed": cdp.allowed})
            cdp.ws.close()
        for process in launches:
            update_children(process.pid, tracked)
        for pid in (tracked | {p.pid for p in launches}) & process_snapshot().keys():
            subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        for path in (data / "logs").glob("backend-*.log"):
            (root / path.name).write_text(safe_text(path.read_text(encoding="utf-8", errors="replace")), encoding="utf-8")
    retained = {p.relative_to(data).as_posix(): digest(p) for p in data.rglob("*") if p.is_file()}
    require(retained, "Default data directory was empty before uninstall")
    result["uninstall"] = command_step([str(install / "uninstall.exe"), "/S"], "uninstall", root, env)
    wait_until(lambda: not install.exists() and registration() is None and not any(p.exists() for p in links),
               30, "Uninstall left installation directory, registry entry or shortcut")
    require(not backend_pids() and not ((tracked | {p.pid for p in launches}) & process_snapshot().keys()),
            "Residual installed process after uninstall")
    require(data.is_dir(), "Uninstall removed default user data")
    require(all((data / name).is_file() and digest(data / name) == value for name, value in retained.items()),
            "Uninstall changed or removed retained user-data files")
    result["data_retained"] = True
    result["data_files_retained"] = len(retained)
    print("INSTALL_STEP uninstall removed application and retained default data", flush=True)


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--startup-timeout", type=float, default=120)
    args = parser.parse_args(argv)
    if not 0 < args.startup_timeout <= 300:
        parser.error("--startup-timeout must be greater than 0 and at most 300 seconds")
    return args


def main(argv=None) -> int:
    args = arguments(argv)
    root = args.evidence_dir.resolve()
    root.mkdir(parents=True, exist_ok=False)
    result = {"ok": False}
    try:
        require(os.name == "nt", "install smoke requires an elevated, interactive Windows runner")
        with fake_service(root / "tts-probe.json", 9880, sentinel=True), fake_service(root / "provider.json") as (port, requests):
            exercise(args.installer.resolve(strict=True), root, args.startup_timeout, result, port)
            require(not any(row["method"] == "POST" for row in requests), "Unexpected model POST request")
            result["model_requests"] = 0
        result["ok"] = True
        print("INSTALL_SMOKE_OK " + json.dumps(result, ensure_ascii=True), flush=True)
        return 0
    except Exception as error:
        result["error"] = safe_text(f"{type(error).__name__}: {error}")
        print("INSTALL_SMOKE_FAILED " + result["error"], flush=True)
        return 1
    finally:
        write_json(root / "result.json", result)


if __name__ == "__main__":
    sys.exit(main())
