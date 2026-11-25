"""Exercise a Windows release artifact on a disposable GitHub-hosted runner."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import subprocess
import threading
import time
import winreg
from zipfile import ZipFile

from bundle_support import digest, sanitized_build_environment
from install_smoke import (
    IDENTIFIER,
    audit_finish,
    audit_start,
    backend_pids,
    cdp_diagnostics,
    check_clean,
    close_windows,
    command_step,
    connect_cdp,
    installed_backend_logs,
    installer_command,
    is_external_url,
    open_calibration_loopback,
    powershell,
    registration,
    require,
    safe_text,
    save_backend_logs,
    settled,
    shortcuts,
    wait_until,
    webview2_debug_policy,
)
from smoke_frozen import REPLY, fake_service, process_snapshot, update_children, write_json

MODEL = "deepseek-flash"
MODEL_LABEL = "DeepSeek-V4.1-Flash"
HOST = "api.deepseek.com"
FIRST = "我每天早晨喝紫苏乌龙。"
SECOND = "我很喜欢柚子果酱。"
CHANGE = "我今天把桌面的灯换成紫色了。"
ASK = "我每天早晨喝的紫苏乌龙，你还记得吗？"
FILL = [f"我收藏第{i:02d}号天文观测模型。" for i in range(1, 34)]
INPUT = "#ws2-composer-input"
LINES = ".ws2-lines article.ws2-line"
SESSION = "localStorage.getItem('amadeus_pc_session_id')"
OLD_ICON_SHA = "392206b573a809997f3ff16fe68f456a52e931c372107eade9572b329bbe3321"


def observation(text, index, confidence):
    return {
        "observation_ref": f"o{index}", "display_text": text, "fact_text": text,
        "semantic": {"subject": "user", "predicate": "rc_fixture", "object": {"text": text},
                     "polarity": "positive", "qualifiers": {}},
        "evidence_kind": "direct_user", "memory_class": "stable_candidate",
        "confidence": confidence, "topic_label": "兴趣和习惯", "relation": "new",
    }


def fixture_completion(messages):
    system = messages[0].get("content", "") if messages else ""
    if system.startswith("Classify retrieval for the current chat turn."):
        query = json.loads(messages[-1]["content"])["current"]
        return "routing", {"search_query": "", "recall_needs": [FIRST] if query == ASK else []}
    if system.startswith("You extract durable user memory"):
        user = json.loads(messages[-1]["content"])["user_text"]
        texts = FILL if user == "".join(FILL) else [user] if user in (FIRST, SECOND) else []
        return "ingest", {"observations": [
            observation(text, index, 0.92 if text == FIRST else 0.99)
            for index, text in enumerate(texts, 1)
        ]}
    return "other-json", {}


def tls_context(directory):
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, HOST)])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(HOST)]), critical=False)
            .sign(key, hashes.SHA256()))
    cert_path = directory / "rc-loopback.crt"
    key_path = directory / "rc-loopback.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    key_path.unlink()
    return context, cert_path


@contextlib.contextmanager
def model_service(root, scratch):
    """Terminate the built-in provider's CONNECT locally without a forwarding path."""
    tls, cert = tls_context(scratch)
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, payload, stream=False):
            if stream:
                body = ("".join(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n" for chunk in payload)
                        + "data: [DONE]\n\n").encode("utf-8")
            else:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_CONNECT(self):
            if self.path != f"{HOST}:443":
                requests.append({"kind": "proxy-denied"})
                self.send_error(403)
                return
            requests.append({"kind": "loopback-connect"})
            self.send_response(200, "Connection established")
            self.end_headers()
            with tls.wrap_socket(self.connection, server_side=True) as client:
                Handler(client, self.client_address, self.server)

        def do_GET(self):
            if self.path != "/models":
                self.send_error(404)
                return
            requests.append({"kind": "models"})
            self.reply({"object": "list", "data": [{"id": MODEL, "object": "model"}]})

        def do_POST(self):
            if self.path != "/chat/completions":
                self.send_error(404)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if payload.get("stream"):
                messages = payload.get("messages", [])
                checking = any(row.get("content") == "Reply OK." for row in messages)
                requests.append({"kind": "chat-check" if checking else "chat",
                                 "model": payload["model"], "effort": payload.get("reasoning_effort")})
                self.reply([
                    {"choices": [{"index": 0, "delta": {"content": "[EMO:neutral] " + REPLY},
                                  "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ], stream=True)
            else:
                kind, content = fixture_completion(payload.get("messages", []))
                requests.append({"kind": kind, "observations": len(content.get("observations", []))})
                self.reply({"choices": [{"message": {"role": "assistant", "content":
                                                     json.dumps(content, ensure_ascii=False)},
                                         "finish_reason": "stop"}]})

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}", cert, requests
        finally:
            server.shutdown()
            thread.join(timeout=2)
            write_json(root / "model-network.json", requests)


@contextlib.contextmanager
def block_external(executables, run_id, root):
    # Apply verified outbound blocks before starting the desktop or its backend.
    names = []
    ranges = "@('0.0.0.0-126.255.255.255','128.0.0.0-255.255.255.255'," \
             "'::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')"
    try:
        enabled = powershell(
            "ConvertTo-Json -InputObject @(Get-NetFirewallProfile -PolicyStore ActiveStore | "
            "ForEach-Object { $_.Enabled.ToString() })"
        )
        require(len(enabled) == 3 and all(item == "True" for item in enabled), "A firewall profile is disabled")
        for exe in executables:
            name = f"AMADEUS-RC-{run_id}-{exe.stem}"
            names.append(name)
            target = str(exe).replace("'", "''")
            rule = powershell(
                f"New-NetFirewallRule -Name '{name}' -DisplayName '{name}' -Program '{target}' "
                f"-Direction Outbound -Action Block -Profile Any -RemoteAddress {ranges} | Out-Null; "
                f"Get-NetFirewallRule -PolicyStore ActiveStore -Name '{name}' | "
                "ForEach-Object { @{enabled=$_.Enabled.ToString(); direction=$_.Direction.ToString(); "
                "action=$_.Action.ToString()} } | ConvertTo-Json -Compress")
            require(rule == {"enabled": "True", "direction": "Outbound", "action": "Block"},
                    "Outbound firewall rule is absent or inactive")
        write_json(root / "firewall.json", {"profiles": enabled, "rules": names, "remote_addresses": ranges})
        yield
    finally:
        for name in names:
            powershell(f"Remove-NetFirewallRule -Name '{name}' -ErrorAction SilentlyContinue; 'null'")


def window_for_pid(pid, title):
    import win32gui
    import win32process

    windows = []

    def visit(window, _):
        if (win32gui.IsWindowVisible(window) and win32gui.GetWindowText(window) == title
                and win32process.GetWindowThreadProcessId(window)[1] == pid):
            windows.append(window)
        return True

    win32gui.EnumWindows(visit, None)
    return windows[0] if windows else None


def save_dialog(window, destination):
    import win32con
    import win32gui

    fields = []

    def visit(child, _):
        if (win32gui.GetClassName(child) == "Edit" and win32gui.IsWindowVisible(child)
                and win32gui.IsWindowEnabled(child)):
            fields.append(child)
        return True

    win32gui.EnumChildWindows(window, visit, None)
    named = [child for child in fields if win32gui.GetDlgCtrlID(child) == 1001]
    fields = named or fields
    require(len(fields) == 1, "Native Save As filename field unavailable")
    win32gui.SendMessage(fields[0], win32con.WM_SETTEXT, 0, str(destination))
    save = win32gui.GetDlgItem(window, win32con.IDOK)
    require(save and win32gui.IsWindowEnabled(save), "Native Save As button unavailable")
    win32gui.PostMessage(save, win32con.BM_CLICK, 0, 0)


def prepare_display(root):
    import win32api
    import win32con

    current = win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS)
    before = [current.PelsWidth, current.PelsHeight]
    mode = None
    if before[0] < 1300 or before[1] < 800:
        index = 0
        while True:
            try:
                candidate = win32api.EnumDisplaySettings(None, index)
            except win32api.error:
                break
            if candidate is None:
                break
            if candidate.PelsWidth >= 1366 and candidate.PelsHeight >= 850 and candidate.BitsPerPel == 32:
                mode = candidate
                break
            index += 1
    changed = win32api.ChangeDisplaySettings(mode, 0) if mode is not None else None
    active = win32api.EnumDisplaySettings(None, win32con.ENUM_CURRENT_SETTINGS)
    write_json(root / "09-display.json", {"screen_before": before, "change_code": changed,
                                         "screen_active": [active.PelsWidth, active.PelsHeight]})


def icon_payloads(ico):
    data = ico.read_bytes()
    count = struct.unpack_from("<H", data, 4)[0]
    for i in range(count):
        size, offset = struct.unpack_from("<II", data, 6 + i * 16 + 8)
        yield hashlib.sha256(data[offset:offset + size]).hexdigest()


def verify_icons(exe, expected, window, root):
    import pefile
    import win32con
    import win32gui
    from PIL import ImageGrab

    require(digest(expected) != OLD_ICON_SHA, "Source still contains the Tauri template icon")
    with pefile.PE(str(exe)) as pe:
        icons = next(row for row in pe.DIRECTORY_ENTRY_RESOURCE.entries if row.id == 3)
        images = {
            hashlib.sha256(pe.get_data(lang.data.struct.OffsetToData, lang.data.struct.Size)).hexdigest()
            for entry in icons.directory.entries for lang in entry.directory.entries
        }
    require(set(icon_payloads(expected)) <= images, "Installed executable icon differs from release source")
    links = shortcuts(exe)
    require(links, "Start Menu shortcut missing")
    for link in links:
        name = str(link).replace("'", "''")
        location = powershell(f"(New-Object -ComObject WScript.Shell).CreateShortcut('{name}').IconLocation | ConvertTo-Json")
        require(not location.split(",")[0].strip(' "')
                or Path(location.rsplit(",", 1)[0].strip(' "')) == exe,
                "Start Menu shortcut uses another icon executable")
    uninstall = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, uninstall) as parent:
        for i in range(winreg.QueryInfoKey(parent)[0]):
            with winreg.OpenKey(parent, winreg.EnumKey(parent, i)) as key:
                try:
                    if winreg.QueryValueEx(key, "DisplayName")[0] == "Amadeus":
                        display_icon = winreg.QueryValueEx(key, "DisplayIcon")[0]
                        break
                except FileNotFoundError:
                    continue
        else:
            raise RuntimeError("Apps & Features DisplayIcon is missing")
    require(Path(display_icon.rsplit(",", 1)[0].strip(' "')) == exe,
            "Apps & Features uses another icon executable")
    icon = (win32gui.SendMessage(window, win32con.WM_GETICON, 2, 0)
            or win32gui.SendMessage(window, win32con.WM_GETICON, 0, 0))
    require(icon, "Desktop window did not supply a title bar icon")
    left, top, right, _ = win32gui.GetWindowRect(window)
    ImageGrab.grab(bbox=(left, top, right, top + 45)).save(root / "11-title-bar.png")
    write_json(root / "11-icon.json", {
        "source_ico_sha256": digest(expected), "matching_embedded_images": len(set(icon_payloads(expected))),
        "start_menu_links": [link.name for link in links], "apps_display_icon": exe.name,
        "title_bar_icon_present": True,
    })
    return "Release ICO matches PE resources; shortcut and uninstall icon point to desktop.exe; native title bar captured."


class Journey:
    def __init__(self, root, exe, data, env, requests, result):
        self.root = root
        self.exe = exe
        self.data = data
        self.env = env
        self.requests = requests
        self.result = result
        self.cdp = None
        self.process = None
        self.tracked = set()
        self.launches = []
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            self.port = reserve.getsockname()[1]
        self.debug = f"--remote-debugging-port={self.port} --no-proxy-server"

    def tick(self):
        for process in self.launches:
            update_children(process.pid, self.tracked)

    def start(self):
        with (self.root / f"desktop-{len(self.launches) + 1}.log").open("wb") as log:
            self.process = subprocess.Popen([str(self.exe)], env=self.env, stdout=log, stderr=subprocess.STDOUT)
        self.launches.append(self.process)
        try:
            self.cdp = connect_cdp(self.port, 90)
        except RuntimeError:
            cdp_diagnostics(self.process, self.port, self.root / f"desktop-{len(self.launches)}.log")
            raise
        # The socket read must allow CDP.call's full response window while the WebView is busy.
        self.cdp.ws.settimeout(15)
        for domain in ("Network", "Log", "Runtime", "Page"):
            self.cdp.call(domain + ".enable")
        self.reload_login()
        self.window = wait_until(lambda: window_for_pid(self.process.pid, "Amadeus"), 10, "Main HWND not found")

    def reload_login(self):
        self.cdp.evaluate("window.__rcBeforeReload = true")
        self.cdp.call("Page.reload", {"ignoreCache": True})
        wait_until(lambda: settled(self.cdp, "!window.__rcBeforeReload && "
                                   "document.querySelector('#salieri-user')?.value === 'Salieri' && "
                                   "document.querySelector('.salieri-login-form button[type=submit]')?.disabled === false"),
                   90, "Login form not ready", self.tick)
        self.cdp.evaluate("localStorage.setItem('amadeus_pc_enable_tts', 'false')")

    def stop(self):
        require(self.cdp is not None, "Desktop DevTools session is unavailable")
        self.save_cdp()
        self.cdp.ws.close()
        self.cdp = None
        self.tick()
        require(close_windows(self.process.pid), "No visible desktop window to close")
        require(self.process.wait(timeout=20) == 0, "Desktop shutdown failed")
        wait_until(lambda: not (self.tracked & process_snapshot().keys()), 25,
                   "Desktop or WebView2 descendants survived close", self.tick)

    def save_cdp(self):
        self.cdp.evaluate("true")
        write_json(self.root / f"cdp-{len(self.launches)}.json", {
            "requests": self.cdp.requests, "errors": self.cdp.errors, "allowed": self.cdp.allowed,
            "sent_frame_types": self.cdp.sent_types,
        })
        require(not any(is_external_url(item["url"]) for item in self.cdp.requests), "External WebView request")
        require("voice.start" not in self.cdp.sent_types, "Voice WebSocket was opened")

    def cleanup(self):
        if self.cdp:
            try:
                self.save_cdp()
            finally:
                self.cdp.ws.close()
        self.tick()
        for pid in (self.tracked | {p.pid for p in self.launches}) & process_snapshot().keys():
            subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"], timeout=15,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def wait(self, expression, description, timeout=20):
        return wait_until(lambda: self.cdp.evaluate(expression), timeout, description, self.tick)

    def find(self, selector):
        return f"document.querySelector({json.dumps(selector)})"

    def click(self, expression):
        self.wait(f"Boolean(({expression}) && !({expression}).disabled)", f"Control unavailable: {expression}")
        self.cdp.evaluate(f"({expression}).click()")

    def button(self, label, scope="body", selector="button"):
        return (f"[...document.querySelectorAll({json.dumps(scope + ' ' + selector)})]"
                f".find(e => e.textContent.trim() === {json.dumps(label)} && e.getClientRects().length)")

    def write(self, selector, value):
        self.wait(f"Boolean({self.find(selector)} && !{self.find(selector)}.disabled)", f"Input unavailable: {selector}")
        self.cdp.evaluate(f"{self.find(selector)}.focus()")
        self.cdp.call("Input.insertText", {"text": value})

    def snap(self, name):
        self.cdp.screenshot(self.root / f"{name}.png")

    def api_result(self, path, payload=None):
        endpoint = json.dumps(path)
        body = json.dumps(json.dumps(payload)) if payload is not None else "null"
        response = self.cdp.call("Runtime.evaluate", {"awaitPromise": True, "returnByValue": True, "expression": f"""
            (async () => {{
                const b = await window.__TAURI_INTERNALS__.invoke('get_local_backend');
                const headers = {{Authorization: 'Bearer ' + b.token}};
                const body = {body};
                const options = body === null ? {{headers}} :
                    {{method: 'POST', headers: {{...headers, 'Content-Type': 'application/json'}}, body}};
                const r = await fetch(b.http_origin + {endpoint}, options);
                const value = await r.json();
                return {{status: r.status, data: r.ok ? value : {{code: value.detail?.code}}}};
            }})()
        """})
        require("exceptionDetails" not in response, f"Local API read failed: {path.split('?')[0]}")
        return response["result"]["value"]

    def api(self, path):
        value = self.api_result(path)
        require(value["status"] == 200, f"Local API returned {value['status']}: {path.split('?')[0]}")
        return value["data"]

    def query(self):
        return "?session_id=" + self.cdp.evaluate(SESSION) + "&worldline=steins_gate"

    def selected(self):
        return next((row["id"] for row in self.api("/api/conversations" + self.query()) if row["is_selected"]), None)

    def messages(self, conversation):
        return self.api(f"/api/conversations/{conversation}/messages" + self.query())

    def sign_in(self):
        self.write("#salieri-pwd", "Gott würfelt nicht")
        self.click(self.find(".salieri-login-form button[type=submit]"))
        self.wait("!document.querySelector('.salieri-login-form') && Boolean(document.querySelector('.ws2'))",
                  "Login did not reveal the workstation", 35)

    def open_section(self, label):
        self.click(self.find('button[aria-label^="系统设置"]'))
        self.click(self.button(label, ".ws2-sys-index", "button .zh") + "?.closest('button')")

    def close_section(self):
        self.click(self.find(".ws2-sys-index .back"))
        self.wait("!document.querySelector('.ws2-sysmenu.open')", "System panel did not close")

    def step(self, number, action):
        started = time.monotonic()
        try:
            detail = action()
            status = "pass"
        except NotCovered as exc:
            status, detail = "not-covered", str(exc)
        except Exception as exc:
            status, detail = "fail", safe_text(f"{type(exc).__name__}: {exc}")
            if self.cdp:
                try:
                    self.snap(f"{number:02d}-failure")
                except Exception:
                    pass
        entry = {"status": status, "detail": detail, "seconds": round(time.monotonic() - started, 2)}
        self.result["checks"][str(number)] = entry
        write_json(self.root / "result.json", self.result)
        print(f"CHECK {number:02d} {status} {detail}", flush=True)
        return status == "pass"

    def login(self):
        location = Path(os.environ["LOCALAPPDATA"]) / IDENTIFIER / "data-location.json"
        require(Path(json.loads(location.read_text(encoding="utf-8"))["path"]) == self.data,
                "Installer did not retain the selected data location")
        require((self.data / "amadeus-data.json").is_file(), "Selected data location marker missing")
        self.snap("02-login")
        self.sign_in()
        self.snap("02-workstation")
        return "Selected data directory via NSIS /DATADIR, checked pointer and marker, signed in through the real form."

    def onboarding(self):
        self.wait("document.querySelector('.ws2-empty-head')?.textContent === '尚未接通'",
                  "Missing-key guide did not appear")
        self.snap("01-missing-key")
        self.click(self.button("填写密钥", ".ws2-empty"))
        self.write('[data-testid="credential-input"]', "rc-fixture-deepseek-only")
        self.click(self.find('.ws2-cx-row.is-active form button[type="submit"]'))
        self.wait("document.querySelector('.ws2-cx-row.is-active .detail')?.textContent.includes('已保存 · 不回显')",
                  "Fake key not saved", 35)
        self.snap("01-key-saved")
        self.click(self.button("测一下", ".ws2-cx-row.is-active .detail"))
        outcome = self.wait("""(() => {
            const label = [...document.querySelectorAll('.ws2-cx-row.is-active .detail b')]
                .find(e => e.textContent === '连接测试');
            const text = label?.nextElementSibling?.textContent.trim();
            return text && !text.startsWith('正在用 ') && !text.startsWith('会用所选模型') && text;
        })()""", "Loopback connection check did not settle", 35)
        self.snap("01-connection")
        require(outcome.startswith("通过 ·"), f"Connection check UI returned: {outcome}")
        self.close_section()
        self.wait("document.querySelector('.ws2-online')?.textContent.trim() === 'ONLINE'", "Workstation went offline", 35)
        self.wait(f"document.querySelector('[aria-label=模型]')?.textContent.includes('{MODEL_LABEL}') && "
                  "document.querySelector('[aria-label=推理强度]')?.textContent.includes('HIGH')",
                  "Default model display name or HIGH effort mismatch")
        self.snap("01-default")
        return f"Missing-key CTA, fake key saved, loopback connection check passed; default {MODEL_LABEL} and HIGH."

    def recover_chat_setup(self):
        # The exact onboarding result is already recorded. Finish the model selection
        # explicitly via the installed API so the independent checks can still run.
        result = self.api_result("/api/providers/connect", {
            "provider_id": "deepseek", "model_id": MODEL, "api_key": "rc-fixture-deepseek-only",
        })
        require(result["status"] == 200 and result["data"]["check"]["ok"],
                f"Fallback local model selection failed: {result['status']} {result['data'].get('code')}")
        print("FIXTURE selected DeepSeek model explicitly through installed API", flush=True)
        self.reload_login()
        self.sign_in()
        self.wait("document.querySelector('.ws2-online')?.textContent.trim() === 'ONLINE'",
                  "Configured fixture did not reach ONLINE", 35)
        self.snap("01-explicit-model")

    def send(self, text, count):
        before = sum(item["kind"] == "chat" for item in self.requests)
        self.write(INPUT, text)
        self.click(self.find('button[aria-label="发送"]'))
        self.wait(f"document.querySelectorAll('{LINES}').length === {count * 2} && "
                  f"[...document.querySelectorAll('.ws2-line-kurisu')].at(-1)?.textContent.includes({json.dumps(REPLY)}) && "
                  f"!{self.find(INPUT)}.disabled", "Chat reply/subtitle did not settle", 90)
        conversation = wait_until(self.selected, 15, "Chat did not select a conversation", self.tick)
        wait_until(lambda: len(self.messages(conversation)) == count * 2, 15, "Chat history not persisted", self.tick)
        require(sum(item["kind"] == "chat" for item in self.requests) > before, "No streamed local model request")
        return conversation

    def chat(self):
        self.conversation = self.send(FIRST, 1)
        require(any(item.get("effort") == "high" for item in self.requests if item["kind"] == "chat"),
                "Chat request did not use reasoning effort high")
        self.snap("03-chat")
        return "Fixed Japanese text visible in the transcript and stored with its user message; loopback stream used high."

    def chart(self):
        return self.api("/api/memory/chart" + self.query() + "&identity_mode=okabe")

    def fact_count(self):
        return self.chart()["counts"]["facts"]

    def star_map(self):
        wait_until(lambda: self.fact_count() == 1, 100, "First turn did not produce a memory point", self.tick)
        self.click(self.find('button[aria-label="MEMORY"]'))
        self.wait('document.querySelector(\'[data-testid=memory-stars]\')?.textContent.trim() === "1 + 0"',
                  "Chart did not display the single ingested memory point", 40)
        self.snap("04-star-map")
        return "Chart route and visible star count show one stable fact created by the first turn."

    def diary(self):
        self.click(self.find('[data-testid="memory-tab-archive"]'))
        self.click(self.find('[data-testid="archive-tab-diary"]'))
        self.wait('Boolean(document.querySelector("[data-testid=archive-diaries-total]") && '
                  '!document.querySelector("#archive-panel-diary")?.textContent.includes("正在读取"))',
                  "Diary archive list did not render")
        self.snap("05-diary")
        self.click(self.find('[data-testid="memory-tab-chart"]'))
        self.click(self.find('[data-testid="memory-back"]'))
        self.wait("!document.querySelector('.ws2-memory-layer')", "Memory overlay did not close")
        return "Opened Archive → Diary and captured the rendered diary list."

    def rollback(self):
        self.send(SECOND, 2)
        wait_until(lambda: self.fact_count() == 2, 100, "Second turn fact did not ingest before rollback", self.tick)
        trigger = self.find(".ws2-line-kurisu .ws2-msg-more")
        self.wait(f"Boolean({trigger} && !{trigger}.disabled)", "First reply's message menu is unavailable")
        self.cdp.evaluate(f"{trigger}.focus()")
        for event in ("rawKeyDown", "keyUp"):
            self.cdp.call("Input.dispatchKeyEvent", {
                "type": event, "key": "Enter", "code": "Enter", "windowsVirtualKeyCode": 13,
            })
        self.click(self.button("回到这里…", "body", '[role="menuitem"]:not([aria-disabled=true])'))
        self.click(self.button("确认回到这里", '[role="alertdialog"]'))
        self.wait(f"document.querySelectorAll('{LINES}').length === 2", "Rollback did not remove one pair from the UI")
        wait_until(lambda: len(self.messages(self.conversation)) == 2 and self.fact_count() == 1,
                   30, "Rollback did not remove one stored pair and its fact", self.tick)
        require(self.messages(self.conversation)[0]["content"] == FIRST, "Rollback changed the first turn")
        self.snap("06-rollback")
        return "Two pairs → one in UI and history API, two facts → one in chart API, original turn retained."

    def restore(self):
        self.open_section("数据")
        self.click(self.button("备份", ".ws2-sys-body"))
        self.click(self.button("立即备份", ".ws2-sys-body"))
        self.wait("document.querySelector('.ws2-sys-body')?.textContent.includes('已备份（')", "Manual backup not confirmed", 60)
        backups = self.api("/api/data/backups")["backups"]
        manual = next(row for row in backups if row["reason"] == "manual")
        require(manual["can_restore"], "Manual backup was not marked restorable")
        self.snap("07-backup")
        self.close_section()
        self.send(CHANGE, 2)
        require(any(row["content"] == CHANGE for row in self.messages(self.conversation)), "Mutation not persisted")
        self.open_section("数据")
        self.click(self.button("备份", ".ws2-sys-body"))
        restore = ("[...document.querySelectorAll('ol[aria-label=备份] > li')]"
                   ".find(e => e.textContent.includes('手动'))?.querySelector('button[aria-label^=\"恢复到 \"]')")
        self.click(restore)
        self.click(self.button("安排恢复", '[role="alertdialog"]'))
        self.wait("document.querySelector('.ws2-sys-body')?.textContent.includes('已安排：')", "Restore was not scheduled")
        require(self.api("/api/data/backups")["restore_pending"] == manual["id"], "Wrong backup scheduled")
        self.snap("07-restore-pending")
        self.stop()
        self.start()
        self.sign_in()
        rows = self.messages(self.conversation)
        require(len(rows) == 2 and rows[0]["content"] == FIRST and all(r["content"] != CHANGE for r in rows),
                "Restored history does not match the manual backup")
        self.wait(f"document.querySelectorAll('{LINES}').length === 2", "Restored history did not appear in the UI", 40)
        self.snap("07-restored")
        return "Manual backup scheduled through UI; restart retained the first pair and removed the later mutation."

    def recall(self):
        self.send("".join(FILL), 2)
        wait_until(lambda: self.fact_count() == 34, 130, "Expected 34 separately ingested stable facts", self.tick)
        self.send(ASK, 3)
        toggle = '[data-testid="memory-mark-refs"]'
        self.wait(f"Boolean({self.find(toggle)})", "Related turn did not show the reference toggle", 40)
        self.click(self.find(toggle))
        self.wait("document.querySelector('[data-testid=memory-refs-open]')?.getClientRects().length",
                  "Reference list did not expand")
        self.snap("08-references")
        self.click(self.find('[data-testid="memory-refs-open"]'))
        plate = self.find('[data-testid="memory-display-plate"]')
        self.wait("Boolean(document.querySelector('[data-testid=memory-ref-hint]'))", "Reference hint missing")
        if self.cdp.evaluate(f"Boolean({plate} && {plate}.getAttribute('aria-pressed') === 'false')"):
            self.click(plate)
        self.wait("""(() => {
            const mark = document.querySelector('[data-testid=plate-ref] circle');
            return Boolean(document.querySelector('[data-testid=memory-ref-hint]') &&
                mark && getComputedStyle(mark).strokeDasharray !== 'none');
        })()""",
                  "Reference chart hint or dashed crosshair missing", 45)
        self.snap("08-crosshair")
        self.click(self.find('[data-testid="memory-back"]'))
        self.wait("!document.querySelector('.ws2-memory-layer')", "Memory overlay did not close")
        return "34 actual stored facts; expanded the related turn’s reference list and followed it to dashed chart crosshair."

    def login_geometry(self):
        import win32con
        import win32gui

        viewport = self.cdp.evaluate("[innerWidth, innerHeight, devicePixelRatio]")
        left, top, right, bottom = win32gui.GetWindowRect(self.window)
        # Validate real HWND resizing and form fit at the smaller supported size.
        win32gui.SetWindowPos(self.window, 0, 0, 0, right - left - 160, bottom - top - 55,
                              win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
        smaller = self.wait(f"innerWidth < {viewport[0]} && [innerWidth, innerHeight]", "Login window did not resize")
        fit = self.cdp.evaluate("""(() => {
            const r = document.querySelector('.salieri-login-form').getBoundingClientRect();
            return r.left >= -1 && r.top >= -1 && r.right <= innerWidth + 1 && r.bottom <= innerHeight + 1;
        })()""")
        self.snap("09-login-resized")
        win32gui.SetWindowPos(self.window, 0, 0, 0, right - left, bottom - top,
                              win32con.SWP_NOMOVE | win32con.SWP_NOZORDER | win32con.SWP_NOACTIVATE)
        write_json(self.root / "09-window.json", {"default_css": viewport, "smaller_css": smaller, "login_fit": fit})
        self.login_size, self.login_fit = viewport, fit

    def window_layout(self):
        self.wait(f"innerWidth === {self.login_size[0]}", "Original workstation size was not restored")
        layout = self.cdp.evaluate("""(() => {
            const root = document.querySelector('.ws2');
            const visible = [document.documentElement, root, ...root.querySelectorAll('*')]
                .filter(e => e instanceof HTMLElement && e.clientWidth && e.getClientRects().length);
            const scrollbars = visible.filter(e => {
                const overflow = getComputedStyle(e).overflowX;
                return overflow === 'scroll' || ((overflow === 'auto' || e === document.documentElement)
                    && e.scrollWidth > e.clientWidth + 1);
            });
            const controls = [...root.querySelectorAll('.ws2-top, .ws2-composer')]
                .map(e => {
                    const r = e.getBoundingClientRect();
                    return [Math.round(r.left), Math.round(r.right)];
                });
            return {scrollbars: scrollbars.map(e => e.className || e.tagName), controls,
                rootOverflowX: getComputedStyle(root).overflowX};
        })()""")
        self.snap("09-workstation")
        write_json(self.root / "09-workstation.json", layout)
        require(self.login_size[:2] == [1280, 720], f"Default viewport measured {self.login_size[:2]}")
        require(self.login_fit and not layout["scrollbars"]
                and all(0 <= left < right <= 1280 for left, right in layout["controls"]),
                f"Login clipping or workstation horizontal scrollbar: {layout}")
        return "Default viewport 1280×720; resized login fits; no horizontal scrollbar in the workstation."

    def export(self):
        from PIL import ImageGrab
        import win32gui

        self.open_section("数据")
        self.click(self.button("导出", ".ws2-sys-body"))
        self.click(self.find('section[aria-labelledby="ws2-data-export"] .ws2-m-foot button'))
        try:
            modal = wait_until(lambda: window_for_pid(self.process.pid, "导出另存为"), 15, "Native Save As not found")
            ImageGrab.grab(bbox=win32gui.GetWindowRect(modal)).save(self.root / "10-save-as.png")
            destination = self.root / "10-conversations.zip"
            save_dialog(modal, destination)
        except RuntimeError as exc:
            raise NotCovered(f"{exc}. Manually choose a path in 导出另存为 and inspect the conversation ZIP.") from exc
        wait_until(lambda: destination.is_file() and destination.stat().st_size, 20, "Export file not written")
        self.wait("Boolean(document.querySelector('[data-testid=export-saved-path]'))", "Export confirmation missing")
        with ZipFile(destination) as archive:
            documents = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist() if name.endswith(".md"))
        require(FIRST in documents and REPLY in documents and SECOND not in documents and CHANGE not in documents,
                "Export did not match the restored conversation")
        self.snap("10-exported")
        self.close_section()
        return f"Saved through native dialog; Markdown ZIP matches restored history; SHA-256 {digest(destination)}."


class NotCovered(RuntimeError):
    pass


def run(args, result):
    import ctypes
    from tempfile import TemporaryDirectory

    require(os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
            and os.name == "nt" and ctypes.windll.shell32.IsUserAnAdmin(),
            "Use the elevated GitHub-hosted Windows runner")
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    prepare_display(args.evidence)
    installer, = args.artifact.rglob("*_x64-setup.exe")
    entries = dict(line.split("  ", 1)[::-1] for line in
                   (args.artifact / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines())
    require(digest(installer) == entries.get(installer.name), "Installer SHA-256 mismatch")
    result["installer"] = {"file": installer.name, "sha256": entries[installer.name],
                           "version": re.search(r"_(.+)_x64-setup", installer.name).group(1)}
    print("INSTALLER " + json.dumps(result["installer"]), flush=True)

    local = Path(os.environ["LOCALAPPDATA"])
    data = check_clean(local, Path(os.environ["RUNNER_TEMP"]) / "amadeus-rc-data")
    logs = installed_backend_logs(data, local / IDENTIFIER)
    require(registration() is None and not backend_pids(), "Existing install or backend on runner")
    env = sanitized_build_environment()
    require(not (Path(os.environ["APPDATA"]) / "Amadeus").exists(), "Existing source data on runner")
    run_id = os.environ["GITHUB_RUN_ID"]
    with TemporaryDirectory(dir=os.environ["RUNNER_TEMP"]) as scratch, \
            model_service(args.evidence, Path(scratch)) as (proxy, cert, requests), \
            fake_service(args.evidence / "tts-probe.json", 9880, sentinel=True) as (_, tts):
        command_step(installer_command(installer, data), "install", args.evidence, env)
        entry = registration()
        require(entry, "Install did not register in HKCU")
        install = Path(entry["location"])
        exe = install / "desktop.exe"
        backend, = install.rglob("amadeus-backend.exe")
        env.update({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy, "ALL_PROXY": proxy,
                    "NO_PROXY": "localhost,127.0.0.1,::1", "SSL_CERT_FILE": str(cert),
                    "AMADEUS_CREDENTIAL_BACKEND": "windows"})
        journey = Journey(args.evidence, exe, data, env, requests, result)
        try:
            with block_external((exe, backend), run_id, args.evidence), webview2_debug_policy(exe.name, journey.debug):
                bookmark = audit_start()
                calibration = open_calibration_loopback()
                try:
                    journey.start()
                    journey.step(11, lambda: verify_icons(exe, args.icon, journey.window, args.evidence))
                    try:
                        journey.login_geometry()
                    except Exception as exc:
                        result["layout_setup_error"] = safe_text(str(exc))
                    if journey.step(2, journey.login):
                        journey.step(9, journey.window_layout)
                        if not journey.step(1, journey.onboarding):
                            journey.recover_chat_setup()
                        if journey.step(3, journey.chat):
                            if journey.step(4, journey.star_map):
                                journey.step(5, journey.diary)
                            if journey.step(6, journey.rollback) and journey.step(7, journey.restore):
                                journey.step(10, journey.export)
                                journey.step(8, journey.recall)
                    if journey.cdp:
                        journey.stop()
                finally:
                    try:
                        journey.cleanup()
                    finally:
                        rows = audit_finish(bookmark, install, calibration, args.evidence / "native-network.json",
                                            allow_desktop_updater=True)
                        require(any(row["Application"].casefold().endswith("\\amadeus-backend.exe") for row in rows),
                                "No audited network connection for installed backend")
                        require(not any(item["method"] == "POST" for item in tts), "Unexpected TTS request")
                        result["network_audit"] = "pass"
                        print("NETWORK audit calibrated, app/backend outbound firewall active, backend loopback only, zero TTS POSTs",
                              flush=True)
        finally:
            save_backend_logs(logs, args.evidence)
            if registration():
                command_step([str(install / "uninstall.exe"), "/S"], "uninstall", args.evidence, env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--icon", type=Path, required=True)
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=False)
    result = {
        "kind": os.environ.get("RUN_KIND"), "source_run": os.environ.get("SOURCE_RUN"),
        "source_revision": os.environ.get("SOURCE_REVISION"), "workflow_run": os.environ.get("GITHUB_RUN_ID"),
        "checks": {str(n): {"status": "not-covered", "detail": "Journey did not reach this check."}
                   for n in range(1, 12)},
    }
    try:
        run(args, result)
    except Exception as exc:
        result["error"] = safe_text(f"{type(exc).__name__}: {exc}")
        print("CHECKLIST_ERROR " + result["error"], flush=True)
    finally:
        write_json(args.evidence / "result.json", result)
    return int("error" in result or any(item["status"] != "pass" for item in result["checks"].values()))


if __name__ == "__main__":
    raise SystemExit(main())
