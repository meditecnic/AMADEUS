"""Exercise the installed application's updater on a disposable GitHub-hosted runner."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
from threading import Thread
import time
from urllib.parse import quote, urlsplit
from urllib.request import ProxyHandler, Request, build_opener

import websocket

from bundle_support import digest, sanitized_build_environment, write_json
from install_smoke import (
    IDENTIFIER,
    backend_pids,
    check_clean,
    close_windows,
    command_step,
    connect_cdp,
    installer_command,
    registration,
    require,
    save_backend_logs,
    settled,
    wait_until,
    webview2_debug_policy,
)
from smoke_frozen import process_snapshot, update_children
from verify_update_signature import verify_update

OLD_VERSION = "0.0.99"
FEED_PORT = 47855
FEED = f"http://127.0.0.1:{FEED_PORT}"
SESSION = "upgrade-rehearsal"
WORLDLINE = "steins_gate"
SCOPE = f"session_id={SESSION}&worldline={WORLDLINE}"
MODEL = "rehearsal-local"
USER_TEXT = "我每天早上都喝黑咖啡。"
REPLY = "毎朝ブラックコーヒーを飲むのね。"
FACT = "每天早上喝黑咖啡"
OPENER = build_opener(ProxyHandler({}))
CONFIG = Path(__file__).resolve().parents[2] / "desktop/src-tauri/tauri.conf.json"


def record(result, key, value):
    result[key] = value
    print(f"{key} {json.dumps(value, ensure_ascii=False, sort_keys=True)}", flush=True)


def prepare_feed(target, feed):
    installer, = target.glob("*_x64-setup.exe")
    source = target / "latest.json"
    signature = installer.with_name(installer.name + ".sig")
    verified = verify_update(installer, signature, CONFIG, source)
    version = json.loads(source.read_text(encoding="utf-8"))["version"]
    manifest = {
        "version": version,
        "notes": "Upgrade rehearsal",
        "platforms": {"windows-x86_64": {
            "signature": signature.read_text(encoding="utf-8").strip(),
            "url": f"{FEED}/{quote(installer.name)}",
        }},
    }
    write_json(feed / "latest.json", manifest)
    return installer, version, verified


@contextmanager
def loopback_service(feed, installer, traffic):
    # Keep the update feed disabled until the explicit click in About.
    enabled = {"manifest": False}
    installer_url = "/" + quote(installer.name)

    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def translate_path(self, path):
            return str(installer if path == installer_url else feed / "latest.json")

        def do_GET(self):
            if self.path == "/v1/models":
                self.respond({"object": "list", "data": [{"id": MODEL, "object": "model"}]})
                return
            if self.path not in ("/latest.json", installer_url):
                self.send_error(404)
                return
            available = enabled["manifest"]
            traffic.append({"get": "manifest" if self.path == "/latest.json" else "installer",
                            "available": available})
            if available:
                super().do_GET()
            else:
                self.send_response(204)
                self.end_headers()

        def respond(self, payload, content_type="application/json"):
            body = payload if isinstance(payload, str) else json.dumps(payload)
            data = body.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", content_type + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if payload.get("stream"):
                traffic.append({"completion": "chat"})
                chunks = [
                    {"choices": [{"index": 0, "delta": {"content": "[EMO:neutral] " + REPLY}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ]
                sse = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
                self.respond(sse + "data: [DONE]\n\n", "text/event-stream")
                return
            content = json.dumps({"title": FACT}) if payload.get("response_format") else f"{FACT}。"
            messages = payload.get("messages", [])
            if messages and "extract durable user memory" in messages[0].get("content", ""):
                traffic.append({"completion": "memory"})
                content = json.dumps({"observations": [{
                    "observation_ref": "o1", "display_text": FACT, "fact_text": FACT,
                    "semantic": {"subject": "user", "predicate": "drinks",
                                 "object": {"name": "black_coffee"}, "polarity": "positive", "qualifiers": {}},
                    "evidence_kind": "direct_user", "memory_class": "stable_candidate",
                    "confidence": 0.96, "topic_label": "饮食", "relation": "new",
                }]})
            self.respond({"choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]})

    with ThreadingHTTPServer(("127.0.0.1", FEED_PORT), Handler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield enabled
        finally:
            server.shutdown()
            thread.join(timeout=5)


def local_backend(cdp):
    result = cdp.call("Runtime.evaluate", {
        "expression": "window.__TAURI_INTERNALS__.invoke('get_local_backend')",
        "awaitPromise": True,
        "returnByValue": True,
    })
    require("exceptionDetails" not in result, "Desktop did not provide its local backend")
    backend = result["result"]["value"]
    origin = urlsplit(backend["http_origin"])
    require(origin.scheme == "http" and origin.hostname == "127.0.0.1" and origin.port,
            "Desktop did not select an IPv4 loopback backend")
    require(backend["ws_origin"] == f"ws://127.0.0.1:{origin.port}", "Backend socket is outside loopback")
    return backend


def api(backend, path, payload=None):
    request = Request(backend["http_origin"] + path, headers={
        "Authorization": "Bearer " + backend["token"], "Content-Type": "application/json",
    }, data=json.dumps(payload).encode("utf-8") if payload is not None else None)
    with OPENER.open(request, timeout=60) as response:
        return json.load(response)


def seed_chat(backend):
    with closing(websocket.create_connection(
        backend["ws_origin"] + f"/ws/chat?session_id={SESSION}",
        origin="http://tauri.localhost", timeout=20, http_no_proxy=["127.0.0.1"],
        subprotocols=["amadeus.local.v1", f"amadeus.token.{backend['token']}"],
    )) as connection:
        connection.send(json.dumps({
            "type": "auth", "protocol_version": 2, "client": "desktop", "conversation_mode": "draft",
            "provider_id": "custom", "model": MODEL, "enable_tts": False, "worldline": WORLDLINE,
            "temperature": 0.0,
        }))
        ready = json.loads(connection.recv())
        require(ready.get("type") == "session.ready", "Synthetic chat authentication failed")
        connection.send(json.dumps({"type": "chat.send", "content": USER_TEXT}))
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            event = json.loads(connection.recv())
            require(event.get("type") != "error", f"Synthetic chat failed ({event.get('code', 'unknown')})")
            if event.get("type") == "turn.completed":
                return event["conversation_id"]
    raise RuntimeError("Synthetic chat did not finish")


def seed_records(backend):
    conversation_id = seed_chat(backend)
    require(conversation_id, "Completed turn did not identify its conversation")
    conversation = api(backend, f"/api/conversations/{conversation_id}?{SCOPE}")
    fact = wait_until(
        lambda: next((item for item in api(
            backend, f"/api/memory/facts?{SCOPE}&identity_mode={conversation['identity_mode']}"
        )["facts"] if item["display_text"] == FACT), None),
        90, "Synthetic memory job did not create the expected fact",
    )
    return {"conversation": conversation["id"], "fact": fact["fact_id"], "mode": conversation["identity_mode"]}


def check_records(backend, ids):
    conversation = ids["conversation"]
    rows = api(backend, f"/api/conversations/{conversation}/messages?{SCOPE}")
    require(any(row["role"] == "user" and USER_TEXT in row["content"] for row in rows), "Synthetic user message missing")
    require(any(row["role"] == "assistant" and REPLY in row["content"] for row in rows), "Synthetic reply missing")
    facts = api(backend, f"/api/memory/facts?{SCOPE}&identity_mode={ids['mode']}")["facts"]
    require(any(item["fact_id"] == ids["fact"] and item["display_text"] == FACT for item in facts),
            "Synthetic fact missing or changed")


def click(cdp, selector, label=None):
    text_match = "true" if label is None else f"el.textContent.trim() === {json.dumps(label)}"
    expression = (
        f"(() => {{ const target = [...document.querySelectorAll({json.dumps(selector)})].find("
        f"el => !el.disabled && ({text_match})); if (!target) return false; target.click(); return true; }})()"
    )
    wait_until(lambda: settled(cdp, expression), 20, f"Could not click {label or selector}")


def ready_window(port, evidence, stage, version):
    cdp = connect_cdp(port, 130)
    try:
        cdp.call("Page.enable")
        wait_until(
            lambda: settled(cdp, "document.querySelector('#salieri-user')?.value === 'Salieri' && "
                            "document.querySelector('.salieri-login-form button[type=submit]')?.disabled === false"),
            130, f"{stage}: login screen unavailable",
        )
        cdp.evaluate("document.querySelector('#salieri-pwd').focus()")
        # Built-in clean-install fixture used by install_smoke.py.
        cdp.call("Input.insertText", {"text": "Gott w\u00fcrfelt nicht"})
        click(cdp, ".salieri-login-form button[type=submit]")
        click(cdp, 'button[aria-label^="系统设置"]')
        click(cdp, ".ws2-sysmenu.open .ws2-sys-item .zh", "关于")
        wait_until(lambda: cdp.evaluate("document.querySelector('.ws2-about-head .ver')?.textContent === "
                                       + json.dumps(f"v{version}")), 20, f"{stage}: About version is not v{version}")
        cdp.screenshot(evidence / f"{stage}.png")
        return cdp
    except BaseException:
        cdp.ws.close()
        raise


def running_app():
    processes = process_snapshot()
    desktop = {pid for pid, (_, name) in processes.items() if name.lower() == "desktop.exe"}
    backends = {pid for pid, (_, name) in processes.items() if name.lower() == "amadeus-backend.exe"}
    if len(desktop) == len(backends) == 1:
        desktop_pid, = desktop
        backend_pid, = backends
        if processes[backend_pid][0] == desktop_pid:
            return desktop_pid, backend_pid
    return None


def close_app(cdp, pids):
    tracked = set(pids)
    update_children(pids[0], tracked)
    cdp.ws.close()
    require(close_windows(pids[0]), "No visible desktop window for WM_CLOSE")
    wait_until(lambda: not (tracked & process_snapshot().keys()) and not backend_pids(), 30,
               "Closing app left a desktop, backend or WebView2 process")


def check_pointer(pointer, data, baseline, backend):
    require(pointer.read_bytes() == baseline, "data-location.json changed")
    location = api(backend, "/api/data/location")
    require(location["source"] == "file" and Path(location["path"]) == data, "App booted with wrong data location")


def quick_check(data):
    found = []
    for db in sorted(data.rglob("*.sqlite3")):
        with closing(sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=5)) as connection:
            require(connection.execute("PRAGMA quick_check").fetchall() == [("ok",)],
                    f"SQLite quick_check failed: {db.name}")
        found.append(db.relative_to(data).as_posix())
    require(set(found) >= {"control.sqlite3", "worldlines/sg/history.sqlite3", "worldlines/sg/memory.sqlite3"},
            "SQLite quick_check missed a required data store")
    return found


def observe_updater_error(cdp):
    cdp.evaluate("""(() => {
        const fetchNative = window.fetch;
        window.fetch = (...args) => {
          const response = fetchNative(...args);
          const url = new URL(args[0], location.href);
          if (url.hostname === 'ipc.localhost' && decodeURIComponent(url.pathname) ===
              '/plugin:updater|download_and_install') {
            response.then(reply => {
              if (reply.headers.get('Tauri-Response') === 'error') {
                return reply.clone().json().then(value => {
                  if (typeof value === 'string' && value.includes('not allowed by ACL')) {
                    window.rehearsalAcl = value;
                  }
                });
              }
            }).catch(() => {});
          }
          return response;
        };
    })()""")


def exercise(args, evidence, result, traffic):
    identity = Path(os.environ["LOCALAPPDATA"]) / IDENTIFIER
    default_data = check_clean(identity.parent)
    require(registration() is None and not backend_pids(), "Expected a clean Windows runner")
    moved_data = Path(os.environ["RUNNER_TEMP"]).resolve() / "upgrade-rehearsal-moved"
    require(not moved_data.exists(), "Move target already exists")
    pointer = identity / "data-location.json"
    feed = evidence / "feed"
    feed.mkdir()
    installer, version, verified = prepare_feed(args.target, feed)
    record(result, "UPDATE_SIGNATURE_OK", {"version": version, **verified})
    record(result, "OLD_INSTALLER", {"version": OLD_VERSION, "sha256": digest(args.old)})
    env = sanitized_build_environment()
    env.update({"AMADEUS_CUSTOM_PROVIDER_BASE_URL": f"{FEED}/v1", "AMADEUS_CUSTOM_PROVIDER_MODEL": MODEL,
                "AMADEUS_CREDENTIAL_BACKEND": "memory", "PYTHON_DOTENV_DISABLED": "1"})
    result["install"] = command_step(installer_command(args.old), "install", evidence, env)
    entry = registration()
    require(entry is not None, "Installed app missing uninstall registration")
    exe = Path(entry["location"]) / "desktop.exe"
    require(exe.is_file(), "Installed desktop.exe missing")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        debug_port = listener.getsockname()[1]
    debug = f"--remote-debugging-port={debug_port}"
    env["WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"] = debug

    with loopback_service(feed, installer, traffic) as enabled, webview2_debug_policy(exe.name, debug):
        with (evidence / "desktop.log").open("wb") as log:
            launch = partial(subprocess.Popen, [str(exe)], env=env, stdout=log, stderr=subprocess.STDOUT)
            initial = launch()
            with closing_cdp(ready_window(debug_port, evidence, "old-before-move", OLD_VERSION)) as cdp:
                backend = local_backend(cdp)
                ids = seed_records(backend)
                check_records(backend, ids)
                record(result, "SEED_VERIFIED", ids)
                require(not pointer.exists(), "Default installation already has a data-location pointer")
                move = api(backend, "/api/data/location/move", {"path": str(moved_data), "acknowledge_warnings": True})
                require(move["restart_required"], "Data move did not request a restart")
                baseline = pointer.read_bytes()
                location = json.loads(baseline)
                require(Path(location["path"]) == moved_data and Path(location["previous_path"]) == default_data,
                        "Data move wrote the wrong pointer")
                initial_pids = wait_until(running_app, 15, "Initial app did not have a single backend")
                require(initial_pids[0] == initial.pid, "Initial WebView2 session attached to the wrong launch")
                close_app(cdp, initial_pids)
            require(initial.wait(timeout=15) == 0, "Old app did not close cleanly after data move")

            old = launch()
            with closing_cdp(ready_window(debug_port, evidence, "old-after-move", OLD_VERSION)) as cdp:
                backend = local_backend(cdp)
                check_pointer(pointer, moved_data, baseline, backend)
                check_records(backend, ids)
                record(result, "DATA_MOVE_OK", {"pointer_sha256": digest(pointer), "version": OLD_VERSION})
                old_pids = wait_until(running_app, 15, "Old app did not have a single backend")
                require(old_pids[0] == old.pid, "Old WebView2 session attached to the wrong launch")
                check_selector = '.ws2-m-sec[aria-labelledby="ws2-about-update"] button'
                wait_until(lambda: cdp.evaluate(
                    f"Array.from(document.querySelectorAll({json.dumps(check_selector)})).some(b => "
                    "!b.disabled && b.textContent.trim() === '检查更新')"
                ), 30, "Automatic update check did not settle")
                start = len(traffic)
                enabled["manifest"] = True
                click(cdp, check_selector, "检查更新")
                offer = json.dumps("新版本 " + version)
                wait_until(lambda: cdp.evaluate(f"document.querySelector('.ws2-update-head b')?.textContent === {offer}"),
                           30, "About did not offer the signed update")
                require({"get": "manifest", "available": True} in traffic[start:], "Manual check missed the loopback feed")
                cdp.screenshot(evidence / "update-offered.png")
                record(result, "UPDATE_OFFERED", version)
                if args.expect == "acl-blocked":
                    observe_updater_error(cdp)
                click(cdp, ".ws2-update button", "下载并安装")
                if args.expect == "acl-blocked":
                    denial = wait_until(lambda: cdp.evaluate("window.rehearsalAcl"), 20, "Expected Tauri ACL denial missing")
                    message = "Command plugin:updater|download_and_install not allowed by ACL"
                    require(denial == message, "Unexpected updater IPC denial")
                    alert = wait_until(lambda: cdp.evaluate("document.querySelector('.ws2-update [role=alert]')?.textContent"),
                                       20, "Download failure did not reach About")
                    require("下载没有完成" in alert, "Unexpected About download result")
                    require(not any(item.get("get") == "installer" for item in traffic),
                            "Updater requested the installer despite the ACL denial")
                    require(running_app() == old_pids and old.poll() is None, "Old app stopped after the ACL denial")
                    cdp.screenshot(evidence / "update-blocked.png")
                    record(result, "ACL_BLOCK_CONFIRMED", {"ipc": message, "ui": alert, "installer_requests": 0,
                                                           "running_version": OLD_VERSION, "pids": old_pids})
                    close_app(cdp, old_pids)
                    return

            require(old.wait(timeout=240) == 0, "Old desktop did not exit cleanly for the updater")
            require({"get": "installer", "available": True} in traffic, "Updater never fetched the signed installer")
            with closing_cdp(ready_window(debug_port, evidence, "updated", version)) as cdp:
                new_pids = wait_until(running_app, 30, "Updated app did not start a single backend")
                require(not (set(new_pids) & set(old_pids)), "An old desktop or backend process survived")
                backend = local_backend(cdp)
                check_pointer(pointer, moved_data, baseline, backend)
                check_records(backend, ids)
                record(result, "UPDATED_APP_OK", {"version": version, "old_pids": old_pids, "new_pids": new_pids,
                                                   "pointer_sha256": digest(pointer), **ids})
                close_app(cdp, new_pids)
            record(result, "SQLITE_QUICK_CHECK_OK", quick_check(moved_data))
            reopened = launch()
            with closing_cdp(ready_window(debug_port, evidence, "reopened", version)) as cdp:
                reopened_pids = wait_until(running_app, 30, "Reopened app did not start a single backend")
                require(reopened_pids[0] == reopened.pid, "Reopen attached to the wrong launch")
                backend = local_backend(cdp)
                check_pointer(pointer, moved_data, baseline, backend)
                check_records(backend, ids)
                record(result, "REOPEN_OK", {"version": version, "pids": reopened_pids})
                close_app(cdp, reopened_pids)


@contextmanager
def closing_cdp(cdp):
    with closing(cdp.ws):
        yield cdp


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--expect", choices=("acl-blocked", "upgraded"), required=True)
    args = parser.parse_args()
    require(sys.platform == "win32" and os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted",
            "Run the upgrade rehearsal on a GitHub-hosted Windows runner")
    args.evidence.mkdir(parents=True)
    result = {"expect": args.expect, "run_id": os.environ["GITHUB_RUN_ID"], "head": os.environ["GITHUB_SHA"]}
    traffic = []
    try:
        exercise(args, args.evidence, result, traffic)
        result["passed"] = True
    finally:
        write_json(args.evidence / "result.json", result)
        write_json(args.evidence / "loopback-requests.json", traffic)
        save_backend_logs(Path(os.environ["LOCALAPPDATA"]) / IDENTIFIER / "logs", args.evidence)


if __name__ == "__main__":
    main()
