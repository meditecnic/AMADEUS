use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Write};
#[cfg(not(windows))]
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{
    atomic::{AtomicBool, Ordering},
    mpsc, Arc, Condvar, Mutex,
};
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};
use tauri_plugin_opener::OpenerExt;

mod backend_logs;
use backend_logs::CappedLog;

mod instance;
use instance::{acquire_instance_lock, backend_runtime_dir, InstanceLock};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

const BACKEND_READY_TIMEOUT: Duration = Duration::from_secs(120);
const READY_PREFIX: &str = "AMADEUS_BACKEND_READY ";
const STARTUP_DIAG_MAX_LINES: usize = 80;
const STARTUP_DIAG_MAX_BYTES: usize = 8 * 1024;
const STARTUP_DIAG_READ_WINDOW_BYTES: u64 = 64 * 1024;
#[cfg(windows)]
const FILE_ATTRIBUTE_REPARSE_POINT_MASK: u32 = 0x0400;
const PICK_PATH_DIRECTORY: &str = "directory";
const PICK_PATH_ZIP: &str = "zip";

#[derive(Clone, serde::Serialize)]
struct LocalBackend {
    version: u8,
    http_origin: String,
    ws_origin: String,
    token: String,
}

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
struct StartupDiagnostics {
    log_dir: String,
    runtime_dir: String,
    app_version: String,
    platform: String,
    startup_log_tail: String,
    stderr_log_tail: String,
    stdout_log_tail: String,
    last_error_summary: String,
}

fn generate_local_token() -> io::Result<String> {
    let mut bytes = [0u8; 32];
    #[cfg(windows)]
    unsafe {
        #[link(name = "bcrypt")]
        extern "system" {
            fn BCryptGenRandom(algorithm: isize, buffer: *mut u8, length: u32, flags: u32) -> i32;
        }
        // BCRYPT_USE_SYSTEM_PREFERRED_RNG; NTSTATUS success is non-negative.
        if BCryptGenRandom(0, bytes.as_mut_ptr(), bytes.len() as u32, 0x2) < 0 {
            return Err(io::Error::new(io::ErrorKind::Other, "local token generation failed"));
        }
    }
    #[cfg(not(windows))]
    File::open("/dev/urandom")?.read_exact(&mut bytes)?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

#[tauri::command]
async fn get_local_backend(
    window: tauri::WebviewWindow,
    backend: tauri::State<'_, Arc<BackendStartup>>,
) -> Result<LocalBackend, String> {
    if window.label() != "main" {
        return Err("local backend is only available to the main window".into());
    }
    let startup = Arc::clone(backend.inner());
    tauri::async_runtime::spawn_blocking(move || {
        let mut result = startup.result.lock().unwrap();
        while result.is_none() {
            result = startup.ready.wait(result).unwrap();
        }
        result.as_ref().unwrap().clone()
    })
    .await
    .map_err(|_| "local backend startup wait failed".to_string())?
}

#[derive(Default)]
struct BackendStartup {
    result: Mutex<Option<Result<LocalBackend, String>>>,
    active_token: Mutex<Option<String>>,
    ready: Condvar,
}

impl BackendStartup {
    fn set_active_token(&self, token: &str) {
        *self.active_token.lock().unwrap() = Some(token.to_string());
    }

    fn active_token(&self) -> Option<String> {
        self.active_token.lock().unwrap().clone()
    }

    fn last_error(&self) -> Option<String> {
        match self.result.lock().unwrap().as_ref() {
            Some(Err(reason)) => Some(reason.clone()),
            _ => None,
        }
    }

    fn finish(&self, result: Result<LocalBackend, String>) {
        *self.result.lock().unwrap() = Some(result);
        self.ready.notify_all();
    }
}

fn parse_backend_ready(line: &str, child_pid: u32) -> Result<u16, String> {
    let json = line.strip_prefix(READY_PREFIX).ok_or("READY prefix missing")?;
    let value: serde_json::Value = serde_json::from_str(json).map_err(|_| "READY invalid JSON")?;
    if value["version"].as_u64() != Some(1) {
        return Err("READY version mismatch".into());
    }
    if value["pid"].as_u64() != Some(child_pid as u64) {
        return Err("READY pid mismatch".into());
    }
    match value["port"].as_u64() {
        Some(port) if (1..=65535).contains(&port) => Ok(port as u16),
        _ => Err("READY port out of range".into()),
    }
}

fn read_backend_stdout(
    stdout: impl io::Read,
    mut log: impl Write,
    pid: u32,
    sender: mpsc::Sender<Result<u16, String>>,
) {
    let mut reader = BufReader::new(stdout);
    let mut line = Vec::new();
    let mut sender = Some(sender);
    loop {
        line.clear();
        match reader.read_until(b'\n', &mut line) {
            Ok(0) => break,
            Ok(_) => {
                // Always drain stdout, even if the log file can no longer be written.
                let _ = log.write_all(&line);
                if line.starts_with(READY_PREFIX.as_bytes()) {
                    if let Some(sender) = sender.take() {
                        let result = std::str::from_utf8(&line)
                            .map_err(|_| "READY invalid JSON".to_string())
                            .and_then(|line| parse_backend_ready(line, pid));
                        let _ = sender.send(result);
                    }
                }
            }
            Err(_) => {
                if let Some(sender) = sender.take() {
                    let _ = sender.send(Err("stdout read failed before READY".into()));
                }
                return;
            }
        }
    }
    if let Some(sender) = sender {
        let _ = sender.send(Err("stdout EOF before READY".into()));
    }
}

fn fail_backend_startup(startup: &BackendStartup, logs: &Path, reason: String) {
    let _ = fs::create_dir_all(logs);
    let _ = writeln!(CappedLog::open(&logs.join("backend-startup.log")), "AMADEUS_BACKEND_FAILED {reason}");
    startup.finish(Err(reason));
}


#[cfg(windows)]
fn create_backend_job() -> io::Result<std::os::windows::io::OwnedHandle> {
    use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
    use windows_sys::Win32::System::JobObjects::{
        CreateJobObjectW, SetInformationJobObject, JobObjectExtendedLimitInformation,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };
    // An unnamed, non-inheritable handle belongs only to this desktop process.
    let raw = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if raw.is_null() {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: CreateJobObjectW returned a new valid handle; OwnedHandle closes it.
    let job = unsafe { OwnedHandle::from_raw_handle(raw) };
    let mut limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION::default();
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    let configured = unsafe {
        SetInformationJobObject(job.as_raw_handle(), JobObjectExtendedLimitInformation,
            (&limits as *const JOBOBJECT_EXTENDED_LIMIT_INFORMATION).cast(),
            std::mem::size_of_val(&limits) as u32)
    };
    if configured == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(job)
}

#[cfg(windows)]
fn assign_backend_job(job: &std::os::windows::io::OwnedHandle, child: &Child) -> io::Result<()> {
    use std::os::windows::io::AsRawHandle;
    // SAFETY: Both borrowed handles remain valid throughout the call.
    if unsafe { windows_sys::Win32::System::JobObjects::AssignProcessToJobObject(
        job.as_raw_handle(), child.as_raw_handle()) } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

struct BackendProcess {
    child: Mutex<Option<Child>>,
    #[cfg(windows)]
    _job: std::os::windows::io::OwnedHandle,
}

impl BackendProcess {
    fn stop(&self) {
        // Hold the lock through shutdown: concurrent event/startup callbacks wait
        // for this stop to finish, then see None instead of stopping twice.
        let mut slot = self.child.lock().expect("backend process lock poisoned");
        if let Some(mut child) = slot.take() {
            let _ = writeln!(io::stderr(), "AMADEUS_DESKTOP_BACKEND_STOP begin");
            // Closing the private pipe asks the backend to run its normal shutdown.
            drop(child.stdin.take());
            let deadline = Instant::now() + Duration::from_secs(5);
            while Instant::now() < deadline {
                if matches!(child.try_wait(), Ok(Some(_))) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            if !matches!(child.try_wait(), Ok(Some(_))) {
                let _ = child.kill();
                let _ = child.wait();
            }
            let _ = writeln!(io::stderr(), "AMADEUS_DESKTOP_BACKEND_STOP end");
        }
    }
}


fn backend_data_dir(app: &tauri::AppHandle) -> tauri::Result<PathBuf> {
    if let Some(path) = std::env::var_os("AMADEUS_DATA_DIR").filter(|value| !value.is_empty()) {
        return Ok(PathBuf::from(path));
    }
    Ok(resolve_data_location(&app.path().app_local_data_dir()?.join("data"))?)
}

fn resolve_data_location(default: &Path) -> io::Result<PathBuf> {
    let file = default.parent().unwrap().join("data-location.json");
    let invalid = |path: &Path, detail: &str| io::Error::new(io::ErrorKind::InvalidData,
        format!("data_location_missing: {}: {detail}", path.display()));
    match fs::symlink_metadata(&file) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(default.to_path_buf()),
        Err(error) => return Err(invalid(&file, &error.to_string())),
        Ok(_) => {},
    }
    let text = fs::read_to_string(&file).map_err(|e| invalid(&file, &e.to_string()))?;
    let value: serde_json::Value = serde_json::from_str(&text).map_err(|e| invalid(&file, &e.to_string()))?;
    let path = value["path"].as_str().map(PathBuf::from)
        .filter(|path| path.is_absolute()).ok_or_else(|| invalid(&file, "invalid absolute path"))?;
    if !path.is_dir() {
        return Err(invalid(&path, "directory does not exist"));
    }
    let marker = fs::read_to_string(path.join("amadeus-data.json"))
        .map_err(|e| invalid(&path, &e.to_string()))?;
    let marker: serde_json::Value = serde_json::from_str(&marker)
        .map_err(|e| invalid(&path, &e.to_string()))?;
    if marker["format"].as_u64() != Some(1) {
        return Err(invalid(&path, "unsupported data marker"));
    }
    Ok(path)
}

#[tauri::command]
async fn restart_app(window: tauri::WebviewWindow, app: tauri::AppHandle) -> Result<(), String> {
    if window.label() != "main" {
        return Err("restart is only available to the main window".into());
    }
    let handle = app.clone();
    tauri::async_runtime::spawn_blocking(move || {
        if let Some(backend) = handle.try_state::<Arc<BackendProcess>>() {
            backend.stop();
        }
    }).await.map_err(|_| "backend shutdown failed".to_string())?;
    if let Some(lock) = app.try_state::<InstanceLock>() {
        lock.release();
    }
    app.restart();
}

const SENSITIVE_KEYS: &[&str] = &[
    "amadeus_local_api_token",
    "x-amadeus-token",
    "authorization",
    "client_secret",
    "refresh_token",
    "access_token",
    "secret_key",
    "id_token",
    "password",
    "api_key",
    "api-key",
    "apikey",
    "passwd",
    "token",
    "pwd",
];

fn starts_with_ascii_case_insensitive(s: &str, prefix: &str) -> bool {
    s.get(..prefix.len())
        .map_or(false, |head| head.eq_ignore_ascii_case(prefix))
}

fn replace_ascii_case_insensitive(haystack: &str, needle: &str, replacement: &str) -> String {
    if needle.is_empty() || haystack.len() < needle.len() {
        return haystack.to_string();
    }
    let hay_lower = haystack.to_ascii_lowercase();
    let needle_lower = needle.to_ascii_lowercase();
    let mut out = String::with_capacity(haystack.len());
    let mut pos = 0usize;
    while pos <= hay_lower.len() {
        if !hay_lower.is_char_boundary(pos) {
            break;
        }
        let Some(rel) = hay_lower[pos..].find(&needle_lower) else {
            out.push_str(&haystack[pos..]);
            break;
        };
        let match_start = pos + rel;
        let match_end = match_start + needle_lower.len();
        if haystack.is_char_boundary(match_start) && haystack.is_char_boundary(match_end) {
            out.push_str(&haystack[pos..match_start]);
            out.push_str(replacement);
            pos = match_end;
        } else {
            let next = haystack[pos..]
                .chars()
                .next()
                .map(|c| pos + c.len_utf8())
                .unwrap_or(haystack.len());
            out.push_str(&haystack[pos..next]);
            pos = next;
        }
    }
    out
}

fn is_win_path_seg_end(c: char) -> bool {
    matches!(
        c,
        '\\' | '/' | '\r' | '\n' | '"' | '\'' | '`' | '<' | '>' | '|' | ';' | ',' | '?' | '*'
    )
}

fn is_posix_path_seg_end(c: char) -> bool {
    matches!(
        c,
        '/' | '\\'
            | ' '
            | '\t'
            | '\r'
            | '\n'
            | '"'
            | '\''
            | '`'
            | '<'
            | '>'
            | '|'
            | ':'
            | ';'
            | ','
            | ')'
            | ']'
            | '}'
            | '?'
            | '#'
    )
}

fn redact_home_paths(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i].is_ascii_alphabetic()
            && (i == 0 || !bytes[i - 1].is_ascii_alphanumeric())
        {
            let rest = &text[i + 1..];
            let prefix_len = if starts_with_ascii_case_insensitive(rest, ":\\\\users\\\\") {
                Some(10usize)
            } else if starts_with_ascii_case_insensitive(rest, ":\\users\\")
                || starts_with_ascii_case_insensitive(rest, ":/users/")
            {
                Some(8usize)
            } else {
                None
            };
            if let Some(plen) = prefix_len {
                let seg_start = i + 1 + plen;
                let mut rel_end = text.len() - seg_start;
                for (offset, ch) in text[seg_start..].char_indices() {
                    if is_win_path_seg_end(ch) {
                        rel_end = offset;
                        break;
                    }
                }
                let seg = &text[seg_start..seg_start + rel_end];
                if !seg.is_empty() && seg != "<USER>" {
                    out.push_str(&text[i..seg_start]);
                    out.push_str("<USER>");
                    i = seg_start + rel_end;
                    continue;
                }
            }
        }

        if bytes[i] == b'/'
            && (i == 0
                || (!bytes[i - 1].is_ascii_alphanumeric()
                    && bytes[i - 1] != b'_'
                    && bytes[i - 1] != b'-'))
        {
            let rest = &text[i..];
            let prefix_len = if rest.starts_with("/home/") {
                Some(6usize)
            } else if rest.starts_with("/Users/") {
                Some(7usize)
            } else {
                None
            };
            if let Some(plen) = prefix_len {
                let seg_start = i + plen;
                let mut rel_end = text.len() - seg_start;
                for (offset, ch) in text[seg_start..].char_indices() {
                    if is_posix_path_seg_end(ch) {
                        rel_end = offset;
                        break;
                    }
                }
                let seg = &text[seg_start..seg_start + rel_end];
                if !seg.is_empty() && seg != "<USER>" {
                    out.push_str(&text[i..seg_start]);
                    out.push_str("<USER>");
                    i = seg_start + rel_end;
                    continue;
                }
            }
        }

        let ch = text[i..].chars().next().unwrap();
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

fn redact_bearer_and_userinfo(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut i = 0usize;
    while i < bytes.len() {
        if text[i..].starts_with("://") {
            let mut stop_rel = None;
            let mut at_rel = None;
            for (offset, ch) in text[i + 3..].char_indices() {
                if ch == '@' {
                    at_rel = Some(offset);
                    break;
                }
                if matches!(ch, '/' | '?' | '#' | ' ' | '\t' | '\r' | '\n' | '"' | '\'') {
                    stop_rel = Some(offset);
                    break;
                }
            }
            if let Some(rel) = at_rel {
                let userinfo = &text[i + 3..i + 3 + rel];
                if stop_rel.is_none() && userinfo.contains(':') {
                    out.push_str("://[REDACTED]@");
                    i = i + 3 + rel + 1;
                    continue;
                }
            }
        }

        let at_ident_start = i == 0 || (!bytes[i - 1].is_ascii_alphanumeric() && bytes[i - 1] != b'_');
        if at_ident_start && starts_with_ascii_case_insensitive(&text[i..], "bearer") {
            let mut ws_end = i + 6;
            while ws_end < bytes.len() && (bytes[ws_end] == b' ' || bytes[ws_end] == b'\t') {
                ws_end += 1;
            }
            if ws_end > i + 6 {
                let mut tok_end = text.len();
                for (offset, ch) in text[ws_end..].char_indices() {
                    if ch.is_whitespace()
                        || matches!(ch, '"' | '\'' | ',' | ';' | ')' | ']' | '}' | '<' | '>')
                    {
                        tok_end = ws_end + offset;
                        break;
                    }
                }
                let tok = &text[ws_end..tok_end];
                if !tok.is_empty() && !tok.starts_with("[REDACTED") {
                    out.push_str("Bearer [REDACTED]");
                    i = tok_end;
                    continue;
                }
            }
        }

        let ch = text[i..].chars().next().unwrap();
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

fn redact_prefixed_keys_and_pairs(text: &str) -> String {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let mut i = 0usize;
    while i < bytes.len() {
        let at_word_start =
            i == 0 || (!bytes[i - 1].is_ascii_alphanumeric() && bytes[i - 1] != b'_' && bytes[i - 1] != b'-');
        let at_ident_start =
            i == 0 || (!bytes[i - 1].is_ascii_alphanumeric() && bytes[i - 1] != b'_');

        if at_word_start {
            let rest = &text[i..];
            if rest.starts_with("sk-") {
                let mut end = i + 3;
                while end < bytes.len()
                    && (bytes[end].is_ascii_alphanumeric() || bytes[end] == b'_' || bytes[end] == b'-')
                {
                    end += 1;
                }
                if end - (i + 3) >= 8 {
                    out.push_str("[REDACTED_KEY]");
                    i = end;
                    continue;
                }
            }
            if rest.starts_with("AIza") {
                let mut end = i + 4;
                while end < bytes.len()
                    && (bytes[end].is_ascii_alphanumeric() || bytes[end] == b'_' || bytes[end] == b'-')
                {
                    end += 1;
                }
                if end - (i + 4) >= 10 {
                    out.push_str("[REDACTED_KEY]");
                    i = end;
                    continue;
                }
            }
            for prefix in ["github_pat_", "ghp_", "gho_"] {
                if rest.starts_with(prefix) {
                    let mut end = i + prefix.len();
                    while end < bytes.len() && (bytes[end].is_ascii_alphanumeric() || bytes[end] == b'_') {
                        end += 1;
                    }
                    if end - (i + prefix.len()) >= 8 {
                        out.push_str("[REDACTED_KEY]");
                        i = end;
                        continue;
                    }
                }
            }
        }

        if at_ident_start {
            let mut hex_end = i;
            while hex_end < bytes.len() && bytes[hex_end].is_ascii_hexdigit() {
                hex_end += 1;
            }
            if hex_end - i == 64
                && (hex_end == bytes.len()
                    || (!bytes[hex_end].is_ascii_alphanumeric() && bytes[hex_end] != b'_'))
            {
                out.push_str("[REDACTED_TOKEN]");
                i = hex_end;
                continue;
            }

            let mut matched_key: Option<&str> = None;
            for key in SENSITIVE_KEYS {
                if starts_with_ascii_case_insensitive(&text[i..], key) {
                    matched_key = Some(key);
                    break;
                }
            }
            if let Some(key) = matched_key {
                let mut cursor = i + key.len();
                if cursor < bytes.len() && (bytes[cursor] == b'"' || bytes[cursor] == b'\'') {
                    cursor += 1;
                }
                while cursor < bytes.len() && (bytes[cursor] == b' ' || bytes[cursor] == b'\t') {
                    cursor += 1;
                }
                if cursor < bytes.len() && (bytes[cursor] == b':' || bytes[cursor] == b'=') {
                    cursor += 1;
                    while cursor < bytes.len() && (bytes[cursor] == b' ' || bytes[cursor] == b'\t') {
                        cursor += 1;
                    }
                    let val_start = cursor;
                    if val_start < bytes.len() && (bytes[val_start] == b'"' || bytes[val_start] == b'\'') {
                        let quote = bytes[val_start];
                        let mut scan = val_start + 1;
                        let mut escaped = false;
                        let mut close_pos = None;
                        while scan < bytes.len() {
                            let b = bytes[scan];
                            if b == b'\r' || b == b'\n' {
                                break;
                            }
                            if escaped {
                                escaped = false;
                                scan += 1;
                                continue;
                            }
                            if b == b'\\' {
                                escaped = true;
                                scan += 1;
                                continue;
                            }
                            if b == quote {
                                close_pos = Some(scan);
                                break;
                            }
                            scan += 1;
                        }
                        if let Some(end_q) = close_pos {
                            let inner = &text[val_start + 1..end_q];
                            if !inner.is_empty() && !inner.starts_with("[REDACTED") {
                                out.push_str(&text[i..=val_start]);
                                out.push_str("[REDACTED]");
                                out.push(quote as char);
                                i = end_q + 1;
                                continue;
                            }
                        }
                    } else if val_start < bytes.len() {
                        let mut val_end = text.len();
                        for (offset, ch) in text[val_start..].char_indices() {
                            if ch.is_whitespace()
                                || matches!(ch, '&' | ',' | ';' | '"' | '\'' | ')' | ']' | '}' | '<' | '>')
                            {
                                val_end = val_start + offset;
                                break;
                            }
                        }
                        let val = &text[val_start..val_end];
                        if !val.is_empty() && !val.starts_with("[REDACTED") {
                            if key.eq_ignore_ascii_case("authorization")
                                && val_end < bytes.len()
                                && (bytes[val_end] == b' ' || bytes[val_end] == b'\t')
                            {
                                let mut cred_start = val_end;
                                while cred_start < bytes.len()
                                    && (bytes[cred_start] == b' ' || bytes[cred_start] == b'\t')
                                {
                                    cred_start += 1;
                                }
                                let mut cred_end = text.len();
                                for (offset, ch) in text[cred_start..].char_indices() {
                                    if ch.is_whitespace()
                                        || matches!(
                                            ch,
                                            '&' | ',' | ';' | '"' | '\'' | ')' | ']' | '}' | '<' | '>'
                                        )
                                    {
                                        cred_end = cred_start + offset;
                                        break;
                                    }
                                }
                                let cred = &text[cred_start..cred_end];
                                if !cred.is_empty() {
                                    if cred.starts_with("[REDACTED") {
                                        out.push_str(&text[i..cred_end]);
                                        i = cred_end;
                                        continue;
                                    }
                                    out.push_str(&text[i..val_start]);
                                    out.push_str("[REDACTED]");
                                    i = cred_end;
                                    continue;
                                }
                            }
                            out.push_str(&text[i..val_start]);
                            out.push_str("[REDACTED]");
                            i = val_end;
                            continue;
                        }
                    }
                }
            }
        }

        let ch = text[i..].chars().next().unwrap();
        out.push(ch);
        i += ch.len_utf8();
    }
    out
}

fn redact_sensitive_with_env_paths(
    text: &str,
    extra_secrets: &[&str],
    env_paths: &[(&str, &str)],
) -> String {
    let mut out = text.to_string();
    for secret in extra_secrets {
        if !secret.is_empty() {
            out = out.replace(secret, "[REDACTED_TOKEN]");
        }
    }
    let mut normalized_envs: Vec<(String, &str)> = env_paths
        .iter()
        .filter_map(|&(raw, placeholder)| {
            let trimmed = raw.trim().trim_end_matches(|c| c == '\\' || c == '/');
            if trimmed.len() >= 3 && trimmed != "/" {
                Some((trimmed.to_string(), placeholder))
            } else {
                None
            }
        })
        .collect();
    // Replace longer specific roots (e.g. %LOCALAPPDATA%/%APPDATA%) before parent home roots.
    normalized_envs.sort_by(|a, b| b.0.len().cmp(&a.0.len()));

    for (trimmed, placeholder) in normalized_envs {
        let forward = trimmed.replace('\\', "/");
        let backslash = trimmed.replace('/', "\\");
        let doubled = backslash.replace('\\', "\\\\");
        let mut seen: Vec<String> = Vec::with_capacity(4);
        for variant in [doubled, trimmed, backslash, forward] {
            if !seen.iter().any(|existing| existing == &variant) {
                out = replace_ascii_case_insensitive(&out, &variant, placeholder);
                seen.push(variant);
            }
        }
    }
    let out = redact_home_paths(&out);
    let out = redact_bearer_and_userinfo(&out);
    redact_prefixed_keys_and_pairs(&out)
}

#[cfg(test)]
fn redact_sensitive_with_homes(
    text: &str,
    extra_secrets: &[&str],
    userprofile: Option<&str>,
    home: Option<&str>,
) -> String {
    let mut env_paths: Vec<(&str, &str)> = Vec::new();
    if let Some(val) = userprofile {
        env_paths.push((val, "%USERPROFILE%"));
    }
    if let Some(val) = home {
        env_paths.push((val, "<HOME>"));
    }
    redact_sensitive_with_env_paths(text, extra_secrets, &env_paths)
}

fn redact_sensitive(text: &str, extra_secrets: &[&str]) -> String {
    let env_values: Vec<(Option<String>, &str)> = [
        ("LOCALAPPDATA", "%LOCALAPPDATA%"),
        ("APPDATA", "%APPDATA%"),
        ("USERPROFILE", "%USERPROFILE%"),
        ("HOME", "<HOME>"),
    ]
    .into_iter()
    .map(|(name, placeholder)| {
        (
            std::env::var_os(name).map(|v| v.to_string_lossy().into_owned()),
            placeholder,
        )
    })
    .collect();
    let env_refs: Vec<(&str, &str)> = env_values
        .iter()
        .filter_map(|(val, placeholder)| val.as_deref().map(|s| (s, *placeholder)))
        .collect();
    redact_sensitive_with_env_paths(text, extra_secrets, &env_refs)
}

fn metadata_is_link_or_reparse(meta: &fs::Metadata) -> bool {
    if meta.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        (meta.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT_MASK) != 0
    }
    #[cfg(not(windows))]
    {
        false
    }
}

fn check_chain_with<F>(path: &Path, mut is_link: F) -> io::Result<()>
where
    F: FnMut(&Path) -> io::Result<bool>,
{
    for ancestor in path.ancestors() {
        if ancestor.as_os_str().is_empty() || ancestor.parent().is_none() {
            continue;
        }
        match is_link(ancestor) {
            Ok(true) => {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "refusing symlink or reparse point in path",
                ));
            }
            Ok(false) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
    }
    Ok(())
}

fn ensure_no_links_in_chain(path: &Path) -> io::Result<()> {
    check_chain_with(path, |ancestor| {
        let meta = fs::symlink_metadata(ancestor)?;
        Ok(metadata_is_link_or_reparse(&meta))
    })
}

fn read_log_window(path: &Path, window_bytes: u64) -> io::Result<String> {
    use std::io::{Read as _, Seek, SeekFrom};

    if window_bytes == 0 {
        return Ok(String::new());
    }
    ensure_no_links_in_chain(path)?;
    let mut file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(String::new()),
        Err(error) => return Err(error),
    };
    let meta = file.metadata()?;
    if metadata_is_link_or_reparse(&meta) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "refusing symlink or reparse point in path",
        ));
    }
    let len = meta.len();
    let seek_pos = len.saturating_sub(window_bytes);
    if seek_pos > 0 {
        file.seek(SeekFrom::Start(seek_pos))?;
    }
    let mut raw = Vec::new();
    file.take(window_bytes).read_to_end(&mut raw)?;

    let mut valid_start = 0usize;
    if seek_pos > 0 {
        while valid_start < raw.len() && (raw[valid_start] & 0b1100_0000) == 0b1000_0000 {
            valid_start += 1;
        }
    }
    let decoded = String::from_utf8_lossy(&raw[valid_start..]);
    let mut text = decoded.replace("\r\n", "\n").replace('\r', "\n");
    if seek_pos > 0 {
        // Drop a potentially split first fragment so secrets straddling the window start cannot survive half-cut.
        if let Some(nl) = text.find('\n') {
            text = text[nl + 1..].to_string();
        } else if let Some(ws_end) = text
            .char_indices()
            .find(|&(_, ch)| ch.is_ascii_whitespace())
            .map(|(idx, ch)| idx + ch.len_utf8())
        {
            text = text[ws_end..].to_string();
        } else {
            text.clear();
        }
    }
    Ok(text)
}

fn bound_log_tail(text: &str, max_lines: usize, max_bytes: usize) -> String {
    if max_lines == 0 || max_bytes == 0 {
        return String::new();
    }
    let normalized = text.replace("\r\n", "\n").replace('\r', "\n");
    let trimmed = normalized.trim_end_matches('\n');
    if trimmed.is_empty() {
        return String::new();
    }

    let lines: Vec<&str> = trimmed.split('\n').collect();
    let mut start_idx = lines.len().saturating_sub(max_lines);
    let mut selected = lines[start_idx..].join("\n");

    while selected.len() > max_bytes && start_idx + 1 < lines.len() {
        start_idx += 1;
        selected = lines[start_idx..].join("\n");
    }

    if selected.len() > max_bytes {
        let mut byte_start = selected.len() - max_bytes;
        while byte_start < selected.len() && !selected.is_char_boundary(byte_start) {
            byte_start += 1;
        }
        selected = selected[byte_start..].to_string();
    }
    selected
}

fn contains_ascii_ci(haystack: &str, needle: &str) -> bool {
    if needle.is_empty() || needle.len() > haystack.len() {
        return false;
    }
    haystack
        .to_ascii_lowercase()
        .contains(&needle.to_ascii_lowercase())
}

fn extract_last_error_summary(
    startup_state_err: Option<&str>,
    startup_tail: &str,
    stdout_tail: &str,
    stderr_tail: &str,
) -> String {
    let startup_line = startup_tail
        .lines()
        .map(str::trim)
        .rfind(|line| !line.is_empty())
        .map(|line| {
            line.strip_prefix("AMADEUS_BACKEND_FAILED ")
                .unwrap_or(line)
                .trim()
                .to_string()
        })
        .filter(|line| !line.is_empty())
        .or_else(|| {
            startup_state_err
                .map(str::trim)
                .filter(|line| !line.is_empty())
                .map(str::to_string)
        });

    let stdout_line = stdout_tail
        .lines()
        .map(str::trim)
        .rfind(|line| line.starts_with("AMADEUS_BACKEND_FAILED "))
        .map(str::to_string);

    let stderr_line = stderr_tail
        .lines()
        .map(str::trim)
        .rfind(|line| {
            !line.is_empty()
                && !line.chars().all(|c| c == '-' || c == '=' || c == '*')
                && (contains_ascii_ci(line, "error")
                    || contains_ascii_ci(line, "exception")
                    || contains_ascii_ci(line, "winerror")
                    || contains_ascii_ci(line, "errno")
                    || contains_ascii_ci(line, "eaddrinuse")
                    || contains_ascii_ci(line, "failed")
                    || contains_ascii_ci(line, "locked")
                    || contains_ascii_ci(line, "denied")
                    || line.contains("拒绝")
                    || line.contains("占用"))
        })
        .or_else(|| {
            stderr_tail
                .lines()
                .map(str::trim)
                .rfind(|line| !line.is_empty() && !line.chars().all(|c| c == '-' || c == '='))
        })
        .map(str::to_string);

    let mut parts: Vec<String> = Vec::new();
    let is_generic_wait_err = startup_line.as_deref().map_or(false, |s| {
        matches!(
            s,
            "stdout EOF before READY"
                | "stdout closed before READY"
                | "stdout read failed before READY"
        )
    });

    if is_generic_wait_err && (stderr_line.is_some() || stdout_line.is_some()) {
        for candidate in [stderr_line, stdout_line, startup_line] {
            if let Some(msg) = candidate {
                if !msg.is_empty() && !parts.iter().any(|existing| existing.contains(&msg)) {
                    parts.push(msg);
                }
            }
        }
    } else {
        for candidate in [startup_line, stderr_line] {
            if let Some(msg) = candidate {
                if !msg.is_empty() && !parts.iter().any(|existing| existing.contains(&msg)) {
                    parts.push(msg);
                }
            }
        }
    }

    parts.join("; ")
}

fn read_redacted_log_tail_with_limits(
    path: &Path,
    extra_secrets: &[&str],
    window_bytes: u64,
    max_lines: usize,
    max_bytes: usize,
) -> io::Result<String> {
    let window = read_log_window(path, window_bytes)?;
    if window.is_empty() {
        return Ok(String::new());
    }
    let redacted = redact_sensitive(&window, extra_secrets);
    Ok(bound_log_tail(&redacted, max_lines, max_bytes))
}

fn read_redacted_log_tail(path: &Path, extra_secrets: &[&str]) -> String {
    match read_redacted_log_tail_with_limits(
        path,
        extra_secrets,
        STARTUP_DIAG_READ_WINDOW_BYTES,
        STARTUP_DIAG_MAX_LINES,
        STARTUP_DIAG_MAX_BYTES,
    ) {
        Ok(content) => content,
        Err(error) => redact_sensitive(&format!("[unavailable: {error}]"), extra_secrets),
    }
}

fn ensure_main_window_label(label: &str) -> Result<(), String> {
    if label == "main" {
        Ok(())
    } else {
        Err("unauthorized window".into())
    }
}

fn ensure_main_window(window: &tauri::WebviewWindow) -> Result<(), String> {
    ensure_main_window_label(window.label())
}

fn resolve_startup_runtime_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    let default_root = app.path().app_local_data_dir().map_err(|e| e.to_string())?;
    let data_override = std::env::var_os("AMADEUS_DATA_DIR").map(PathBuf::from);
    Ok(backend_runtime_dir(data_override.as_deref(), &default_root))
}

fn collect_startup_diagnostics(
    runtime_dir: &Path,
    app_version: &str,
    startup_state_err: Option<&str>,
    extra_secrets: &[&str],
) -> StartupDiagnostics {
    let log_dir = runtime_dir.join("logs");
    let startup_log_tail = read_redacted_log_tail(&log_dir.join("backend-startup.log"), extra_secrets);
    let stderr_log_tail = read_redacted_log_tail(&log_dir.join("backend-stderr.log"), extra_secrets);
    let stdout_log_tail = read_redacted_log_tail(&log_dir.join("backend-stdout.log"), extra_secrets);
    let raw_summary = extract_last_error_summary(
        startup_state_err,
        &startup_log_tail,
        &stdout_log_tail,
        &stderr_log_tail,
    );
    let last_error_summary = redact_sensitive(&raw_summary, extra_secrets);
    StartupDiagnostics {
        log_dir: redact_sensitive(&log_dir.to_string_lossy(), extra_secrets),
        runtime_dir: redact_sensitive(&runtime_dir.to_string_lossy(), extra_secrets),
        app_version: app_version.to_string(),
        platform: format!("{}-{}", std::env::consts::OS, std::env::consts::ARCH),
        startup_log_tail,
        stderr_log_tail,
        stdout_log_tail,
        last_error_summary,
    }
}

#[tauri::command]
async fn get_startup_diagnostics(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
) -> Result<StartupDiagnostics, String> {
    ensure_main_window(&window)?;
    let runtime_dir = resolve_startup_runtime_dir(&app)?;
    let app_version = app.package_info().version.to_string();
    let (active_token, last_error) = match app.try_state::<Arc<BackendStartup>>() {
        Some(startup) => (startup.active_token(), startup.last_error()),
        None => (None, None),
    };
    tauri::async_runtime::spawn_blocking(move || {
        let extra_secrets: Vec<&str> = active_token.as_deref().into_iter().collect();
        Ok(collect_startup_diagnostics(
            &runtime_dir,
            &app_version,
            last_error.as_deref(),
            &extra_secrets,
        ))
    })
    .await
    .map_err(|_| "startup diagnostics collection failed".to_string())?
}

#[tauri::command]
async fn open_startup_log_dir(
    window: tauri::WebviewWindow,
    app: tauri::AppHandle,
) -> Result<(), String> {
    ensure_main_window(&window)?;
    let runtime_dir = resolve_startup_runtime_dir(&app)?;
    tauri::async_runtime::spawn_blocking(move || {
        let log_dir = runtime_dir.join("logs");
        ensure_no_links_in_chain(&runtime_dir).map_err(|e| e.to_string())?;
        ensure_no_links_in_chain(&log_dir).map_err(|e| e.to_string())?;
        let target = if log_dir.is_dir() {
            log_dir
        } else if runtime_dir.is_dir() {
            runtime_dir
        } else {
            return Err("startup log directory does not exist".into());
        };
        ensure_no_links_in_chain(&target).map_err(|e| e.to_string())?;
        app.opener()
            .open_path(target.to_string_lossy().as_ref(), None::<&str>)
            .map_err(|e| e.to_string())
    })
    .await
    .map_err(|_| "open startup log directory task failed".to_string())?
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PickerKind {
    Directory,
    Zip,
}

fn parse_picker_kind(kind: &str) -> Result<PickerKind, String> {
    match kind {
        PICK_PATH_DIRECTORY => Ok(PickerKind::Directory),
        PICK_PATH_ZIP => Ok(PickerKind::Zip),
        _ => Err(format!("invalid picker kind: {kind}")),
    }
}

#[cfg(target_os = "windows")]
mod win32_picker {
    use super::PickerKind;
    use std::ffi::c_void;

    const COINIT_APARTMENTTHREADED: u32 = 0x2;
    const S_OK: i32 = 0;
    const S_FALSE: i32 = 1;
    const RPC_E_CHANGED_MODE: i32 = 0x8001_0106u32 as i32;

    const BIF_RETURNONLYFSDIRS: u32 = 0x0000_0001;
    const BIF_NEWDIALOGSTYLE: u32 = 0x0000_0040;
    const BIF_USENEWUI: u32 = 0x0000_0050;

    const OFN_FILEMUSTEXIST: u32 = 0x0000_1000;
    const OFN_PATHMUSTEXIST: u32 = 0x0000_0800;
    const OFN_NOCHANGEDIR: u32 = 0x0000_0008;

    #[allow(non_camel_case_types, dead_code)]
    #[repr(C)]
    pub(super) struct BROWSEINFOW {
        hwnd_owner: *mut c_void,
        pidl_root: *const c_void,
        psz_display_name: *mut u16,
        lpsz_title: *const u16,
        ul_flags: u32,
        lpfn: Option<unsafe extern "system" fn(*mut c_void, u32, isize, isize) -> i32>,
        l_param: isize,
        i_image: i32,
    }

    #[allow(non_camel_case_types, dead_code)]
    #[repr(C)]
    pub(super) struct OPENFILENAMEW {
        l_struct_size: u32,
        hwnd_owner: *mut c_void,
        h_instance: *mut c_void,
        lpstr_filter: *const u16,
        lpstr_custom_filter: *mut u16,
        n_max_cust_filter: u32,
        n_filter_index: u32,
        lpstr_file: *mut u16,
        n_max_file: u32,
        lpstr_file_title: *mut u16,
        n_max_file_title: u32,
        lpstr_initial_dir: *const u16,
        lpstr_title: *const u16,
        flags: u32,
        n_file_offset: u16,
        n_file_extension: u16,
        lpstr_def_ext: *const u16,
        l_cust_data: isize,
        lpfn_hook: Option<unsafe extern "system" fn(*mut c_void, u32, usize, isize) -> usize>,
        lp_template_name: *const u16,
        pv_reserved: *mut c_void,
        dw_reserved: u32,
        flags_ex: u32,
    }

    #[link(name = "ole32")]
    extern "system" {
        fn CoInitializeEx(pv_reserved: *mut c_void, dw_co_init: u32) -> i32;
        fn CoUninitialize();
        fn CoTaskMemFree(pv: *mut c_void);
    }

    #[link(name = "shell32")]
    extern "system" {
        fn SHBrowseForFolderW(lpbi: *mut BROWSEINFOW) -> *mut c_void;
        fn SHGetPathFromIDListW(pidl: *const c_void, psz_path: *mut u16) -> i32;
    }

    #[link(name = "comdlg32")]
    extern "system" {
        fn GetOpenFileNameW(lpofn: *mut OPENFILENAMEW) -> i32;
        fn CommDlgExtendedError() -> u32;
    }

    struct ComApartmentGuard;

    impl ComApartmentGuard {
        fn init_apartment() -> Result<Option<Self>, String> {
            // SAFETY: pv_reserved must be null; COINIT_APARTMENTTHREADED is required by shell dialogs.
            let hr = unsafe { CoInitializeEx(std::ptr::null_mut(), COINIT_APARTMENTTHREADED) };
            if hr == S_OK || hr == S_FALSE {
                Ok(Some(Self))
            } else if hr == RPC_E_CHANGED_MODE {
                Ok(None)
            } else {
                Err(format!("COM initialization failed (0x{:08x})", hr as u32))
            }
        }
    }

    impl Drop for ComApartmentGuard {
        fn drop(&mut self) {
            // SAFETY: Balanced with successful CoInitializeEx returning S_OK or S_FALSE on this thread.
            unsafe { CoUninitialize() };
        }
    }

    struct PidlGuard(*mut c_void);

    impl Drop for PidlGuard {
        fn drop(&mut self) {
            if !self.0.is_null() {
                // SAFETY: PIDL was allocated by SHBrowseForFolderW's shell allocator.
                unsafe { CoTaskMemFree(self.0) };
            }
        }
    }

    fn utf16_z_to_string(buf: &[u16]) -> Result<String, String> {
        let len = buf.iter().position(|&unit| unit == 0).unwrap_or(buf.len());
        String::from_utf16(&buf[..len]).map_err(|_| "selected path contains invalid UTF-16".into())
    }

    fn pick_directory(owner_hwnd: isize) -> Result<Option<String>, String> {
        let _com = ComApartmentGuard::init_apartment()?;
        let mut display_name = vec![0u16; 260];
        let title: Vec<u16> = "选择文件夹".encode_utf16().chain(Some(0)).collect();
        let mut info = BROWSEINFOW {
            hwnd_owner: owner_hwnd as *mut c_void,
            pidl_root: std::ptr::null(),
            psz_display_name: display_name.as_mut_ptr(),
            lpsz_title: title.as_ptr(),
            ul_flags: BIF_RETURNONLYFSDIRS | BIF_NEWDIALOGSTYLE | BIF_USENEWUI,
            lpfn: None,
            l_param: 0,
            i_image: 0,
        };
        // SAFETY: All pointers in BROWSEINFOW reference stack/heap buffers alive across the modal call.
        let pidl = unsafe { SHBrowseForFolderW(&mut info) };
        if pidl.is_null() {
            return Ok(None);
        }
        let _pidl_guard = PidlGuard(pidl);
        let mut path_buf = vec![0u16; 1024];
        // SAFETY: path_buf is at least MAX_PATH (260) UTF-16 units wide and PIDL is valid.
        if unsafe { SHGetPathFromIDListW(pidl, path_buf.as_mut_ptr()) } == 0 {
            return Err("selected folder does not have a filesystem path".into());
        }
        let path = utf16_z_to_string(&path_buf)?;
        if path.is_empty() {
            Ok(None)
        } else {
            Ok(Some(path))
        }
    }

    fn pick_zip(owner_hwnd: isize) -> Result<Option<String>, String> {
        let _com = ComApartmentGuard::init_apartment()?;
        let filter: Vec<u16> = "Zip Archives\0*.zip\0All Files\0*.*\0\0"
            .encode_utf16()
            .collect();
        let title: Vec<u16> = "选择 Zip 压缩包".encode_utf16().chain(Some(0)).collect();
        let def_ext: Vec<u16> = "zip".encode_utf16().chain(Some(0)).collect();
        let mut file_buf = vec![0u16; 32768];
        let mut ofn = OPENFILENAMEW {
            l_struct_size: std::mem::size_of::<OPENFILENAMEW>() as u32,
            hwnd_owner: owner_hwnd as *mut c_void,
            h_instance: std::ptr::null_mut(),
            lpstr_filter: filter.as_ptr(),
            lpstr_custom_filter: std::ptr::null_mut(),
            n_max_cust_filter: 0,
            n_filter_index: 1,
            lpstr_file: file_buf.as_mut_ptr(),
            n_max_file: file_buf.len() as u32,
            lpstr_file_title: std::ptr::null_mut(),
            n_max_file_title: 0,
            lpstr_initial_dir: std::ptr::null(),
            lpstr_title: title.as_ptr(),
            flags: OFN_FILEMUSTEXIST | OFN_PATHMUSTEXIST | OFN_NOCHANGEDIR,
            n_file_offset: 0,
            n_file_extension: 0,
            lpstr_def_ext: def_ext.as_ptr(),
            l_cust_data: 0,
            lpfn_hook: None,
            lp_template_name: std::ptr::null(),
            pv_reserved: std::ptr::null_mut(),
            dw_reserved: 0,
            flags_ex: 0,
        };
        // SAFETY: All pointers in OPENFILENAMEW remain valid and zero-terminated through GetOpenFileNameW.
        if unsafe { GetOpenFileNameW(&mut ofn) } == 0 {
            // SAFETY: CommDlgExtendedError reads the last common-dialog error on the calling thread.
            let err = unsafe { CommDlgExtendedError() };
            if err == 0 {
                return Ok(None);
            }
            return Err(format!("file dialog failed (0x{err:04x})"));
        }
        let path = utf16_z_to_string(&file_buf)?;
        if path.is_empty() {
            Ok(None)
        } else {
            Ok(Some(path))
        }
    }

    pub(super) fn pick_path(kind: PickerKind, owner_hwnd: isize) -> Result<Option<String>, String> {
        match kind {
            PickerKind::Directory => pick_directory(owner_hwnd),
            PickerKind::Zip => pick_zip(owner_hwnd),
        }
    }
}

#[cfg(target_os = "windows")]
fn pick_path_native(kind: PickerKind, owner_hwnd: isize) -> Result<Option<String>, String> {
    win32_picker::pick_path(kind, owner_hwnd)
}

#[cfg(all(not(target_os = "windows"), unix))]
fn run_picker_command(program: &str, args: &[&str]) -> io::Result<Option<String>> {
    let output = Command::new(program).args(args).output()?;
    if !output.status.success() {
        return Ok(None);
    }
    let path = String::from_utf8_lossy(&output.stdout)
        .trim_end_matches(&['\r', '\n'][..])
        .to_string();
    if path.is_empty() {
        Ok(None)
    } else {
        Ok(Some(path))
    }
}

#[cfg(target_os = "macos")]
fn pick_path_native(kind: PickerKind, _owner_hwnd: isize) -> Result<Option<String>, String> {
    let script = match kind {
        PickerKind::Directory => r#"POSIX path of (choose folder with prompt "选择文件夹")"#,
        PickerKind::Zip => {
            r#"POSIX path of (choose file of type {"public.zip-archive", "zip"} with prompt "选择 Zip 压缩包")"#
        }
    };
    match run_picker_command("osascript", &["-e", script]) {
        Ok(result) => Ok(result),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

#[cfg(all(not(target_os = "windows"), unix, not(target_os = "macos")))]
fn pick_path_native(kind: PickerKind, _owner_hwnd: isize) -> Result<Option<String>, String> {
    let zenity_args: &[&str] = match kind {
        PickerKind::Directory => &["--file-selection", "--directory", "--title=选择文件夹"],
        PickerKind::Zip => &[
            "--file-selection",
            "--title=选择 Zip 压缩包",
            "--file-filter=Zip Archives (*.zip) | *.zip *.ZIP",
            "--file-filter=All Files | *",
        ],
    };
    match run_picker_command("zenity", zenity_args) {
        Ok(result) => return Ok(result),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(error.to_string()),
    }

    let kdialog_args: &[&str] = match kind {
        PickerKind::Directory => &["--getexistingdirectory"],
        PickerKind::Zip => &["--getopenfilename", "", "*.zip|Zip Archives (*.zip)"],
    };
    match run_picker_command("kdialog", kdialog_args) {
        Ok(result) => Ok(result),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

#[cfg(not(any(target_os = "windows", unix)))]
fn pick_path_native(_kind: PickerKind, _owner_hwnd: isize) -> Result<Option<String>, String> {
    Ok(None)
}

static PICKER_IN_FLIGHT: AtomicBool = AtomicBool::new(false);

struct PickerSingleFlightGuard;

impl Drop for PickerSingleFlightGuard {
    fn drop(&mut self) {
        PICKER_IN_FLIGHT.store(false, Ordering::Release);
    }
}

fn try_acquire_picker_guard() -> Option<PickerSingleFlightGuard> {
    if PICKER_IN_FLIGHT
        .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
        .is_ok()
    {
        Some(PickerSingleFlightGuard)
    } else {
        None
    }
}

#[tauri::command]
async fn pick_path(
    window: tauri::WebviewWindow,
    kind: String,
) -> Result<Option<String>, String> {
    ensure_main_window(&window)?;
    let parsed = parse_picker_kind(&kind)?;
    #[cfg(target_os = "windows")]
    let owner_hwnd: isize = window.hwnd().ok().map(|h| h.0 as isize).unwrap_or(0);
    #[cfg(not(target_os = "windows"))]
    let owner_hwnd: isize = 0;

    let Some(guard) = try_acquire_picker_guard() else {
        return Ok(None);
    };
    tauri::async_runtime::spawn_blocking(move || {
        // Hold the single-flight guard inside the OS picker thread until the modal returns or unwinds.
        let _guard = guard;
        std::thread::spawn(move || {
            let _thread_guard = _guard;
            pick_path_native(parsed, owner_hwnd)
        })
        .join()
        .map_err(|_| "native path picker thread panicked".to_string())?
    })
    .await
    .map_err(|_| "native path picker task failed".to_string())?
}

fn start_backend(
    app: &tauri::AppHandle,
    data_dir: &Path,
    logs: &Path,
    token: String,
    startup: Arc<BackendStartup>,
) -> Result<Arc<BackendProcess>, Box<dyn std::error::Error>> {
    startup.set_active_token(&token);
    let logs = logs.to_path_buf();
    let stdout_log = CappedLog::open(&logs.join("backend-stdout.log"));
    let mut stderr_log = CappedLog::open(&logs.join("backend-stderr.log"));
    let backend = app.path().resource_dir()?.join("backend/amadeus-backend.exe");
    let mut command = Command::new(backend);
    command
        .env("AMADEUS_DATA_DIR", &data_dir)
        .env("AMADEUS_APP_VERSION", app.package_info().version.to_string())
        .env("AMADEUS_LOCAL_API_TOKEN", &token)
        .env("AMADEUS_PARENT_PID", std::process::id().to_string())
        .env("BACKEND_PORT", "0")
        .env("PYTHON_DOTENV_DISABLED", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command.env_remove("AMADEUS_LOCATION_FILE");
    if std::env::var_os("AMADEUS_DATA_DIR").filter(|value| !value.is_empty()).is_none() {
        command.env("AMADEUS_LOCATION_FILE", app.path().app_local_data_dir()?.join("data-location.json"));
    }
    command.env("AMADEUS_INSTALL_DIR", app.path().resource_dir()?);
    #[cfg(windows)]
    command.creation_flags(0x0800_0000);
    #[cfg(windows)]
    let job = create_backend_job()?;
    let mut child = command.spawn()?;
    #[cfg(windows)]
    if let Err(error) = assign_backend_job(&job, &child) {
        // Never leave an uncontained backend running after a setup failure.
        let _ = child.kill();
        let _ = child.wait();
        return Err(error.into());
    }
    let started = Instant::now();
    let pid = child.id();
    let stdout = child.stdout.take().expect("stdout was configured as a pipe");
    let mut stderr = child.stderr.take().expect("stderr was configured as a pipe");
    std::thread::spawn(move || { let _ = io::copy(&mut stderr, &mut stderr_log); });
    let backend = Arc::new(BackendProcess {
        child: Mutex::new(Some(child)),
        #[cfg(windows)]
        _job: job,
    });
    let (sender, receiver) = mpsc::channel();
    std::thread::spawn(move || read_backend_stdout(stdout, stdout_log, pid, sender));
    let process = Arc::clone(&backend);
    std::thread::spawn(move || {
        let result = receiver.recv_timeout(BACKEND_READY_TIMEOUT.saturating_sub(started.elapsed()));
        let result = match result {
            Ok(result) => result,
            Err(mpsc::RecvTimeoutError::Timeout) => Err("READY timeout after 120 seconds".into()),
            Err(mpsc::RecvTimeoutError::Disconnected) => Err("stdout closed before READY".into()),
        };
        match result {
            Ok(port) => startup.finish(Ok(LocalBackend {
                version: 1,
                http_origin: format!("http://127.0.0.1:{port}"),
                ws_origin: format!("ws://127.0.0.1:{port}"),
                token,
            })),
            Err(reason) => {
                fail_backend_startup(&startup, &logs, reason);
                process.stop();
            }
        }
    });
    Ok(backend)
}

fn show_startup_error(message: &str) {
    #[cfg(windows)]
    unsafe {
        #[link(name = "user32")]
        extern "system" {
            fn MessageBoxW(window: isize, text: *const u16, caption: *const u16, flags: u32) -> i32;
        }
        let text: Vec<u16> = message.encode_utf16().chain(Some(0)).collect();
        let caption: Vec<u16> = "Amadeus".encode_utf16().chain(Some(0)).collect();
        MessageBoxW(0, text.as_ptr(), caption.as_ptr(), 0x10);
    }
    #[cfg(not(windows))]
    eprintln!("{message}");
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        // Tauri initializes plugins during build, before creating configured WebViews.
        // A rejected second instance must not create a controller and then process::exit.
        .plugin(tauri::plugin::Builder::<_, ()>::new("instance-lock")
            .setup(|app, _| {
                let data_override = std::env::var_os("AMADEUS_DATA_DIR").map(PathBuf::from);
                let runtime_dir = backend_runtime_dir(data_override.as_deref(), &app.path().app_local_data_dir()?);
                match acquire_instance_lock(&runtime_dir) {
                    Ok(lock) => { app.manage(lock); }
                    Err(error) => {
                        if cfg!(windows) && error.raw_os_error() == Some(32) {
                            show_startup_error("Amadeus 已经在运行。");
                        } else {
                            show_startup_error("无法锁定数据目录，Amadeus 无法启动。");
                        }
                        std::process::exit(1);
                    }
                }
                Ok(())
            })
            .build())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            get_local_backend,
            restart_app,
            get_startup_diagnostics,
            open_startup_log_dir,
            pick_path
        ])
        .setup(|app| {
            let startup = Arc::new(BackendStartup::default());
            app.manage(Arc::clone(&startup));
            let data_override = std::env::var_os("AMADEUS_DATA_DIR").map(PathBuf::from);
            let runtime_dir = backend_runtime_dir(data_override.as_deref(), &app.path().app_local_data_dir()?);
            let logs = runtime_dir.join("logs");
            backend_logs::rotate(&logs);
            let data_dir = match backend_data_dir(app.handle()) {
                Ok(path) => path,
                Err(error) => {
                    fail_backend_startup(&startup, &logs, error.to_string());
                    return Ok(());
                }
            };
            if let Some(window) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::Destroyed = event {
                        if let Some(backend) = handle.try_state::<Arc<BackendProcess>>() {
                            backend.stop();
                        }
                        if handle.webview_windows().is_empty() {
                            handle.exit(0);
                        }
                    }
                });
            }
            let result = generate_local_token()
                .map_err(Into::into)
                .and_then(|token| start_backend(app.handle(), &data_dir, &logs, token, Arc::clone(&startup)));
            match result {
                Ok(backend) => { app.manage(backend); }
                Err(error) => fail_backend_startup(&startup, &logs,
                    format!("unable to start backend: {error}")),
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Amadeus")
        .run(|app, event| {
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                if let Some(backend) = app.try_state::<Arc<BackendProcess>>() {
                    backend.stop();
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(windows)]
    fn lifetime_probe() -> (Child, std::os::windows::io::OwnedHandle) {
        let job = create_backend_job().unwrap();
        let mut child = Command::new("cmd.exe").args(["/C", "more >NUL"])
            .stdin(Stdio::piped()).stdout(Stdio::null()).stderr(Stdio::null())
            .creation_flags(0x0800_0000).spawn().unwrap();
        if let Err(error) = assign_backend_job(&job, &child) {
            let _ = child.kill();
            let _ = child.wait();
            panic!("assign probe to job: {error}");
        }
        (child, job)
    }

    #[cfg(windows)]
    #[test]
    fn closing_job_terminates_a_child_with_stdin_still_open() {
        let (mut child, job) = lifetime_probe();
        assert!(child.try_wait().unwrap().is_none());
        drop(job);
        let deadline = Instant::now() + Duration::from_secs(3);
        while Instant::now() < deadline && child.try_wait().unwrap().is_none() {
            std::thread::sleep(Duration::from_millis(20));
        }
        let exited = child.try_wait().unwrap().is_some();
        if !exited {
            let _ = child.kill();
        }
        let _ = child.wait();
        assert!(exited, "closing the job left the child alive");
    }

    #[cfg(windows)]
    #[test]
    fn concurrent_stops_close_stdin_and_finish_only_once() {
        let (child, job) = lifetime_probe();
        let backend = Arc::new(BackendProcess { child: Mutex::new(Some(child)), _job: job });
        let other = Arc::clone(&backend);
        let started = Instant::now();
        let stopping = std::thread::spawn(move || other.stop());
        backend.stop();
        stopping.join().unwrap();
        backend.stop();
        assert!(backend.child.lock().unwrap().is_none());
        assert!(started.elapsed() < Duration::from_secs(4), "stdin EOF did not stop the probe");
    }

    #[test]
    fn missing_or_invalid_location_never_creates_empty_data() {
        let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos();
        let base = std::env::temp_dir().join(format!("amadeus-location-{}-{nonce}", std::process::id()));
        fs::create_dir_all(&base).unwrap();
        let default = base.join("data");
        let target = base.join("missing-target");
        let file = base.join("data-location.json");
        assert_eq!(resolve_data_location(&default).unwrap(), default);
        assert!(!default.exists());
        fs::write(&file, serde_json::json!({"path": target}).to_string()).unwrap();
        let error = resolve_data_location(&default).unwrap_err().to_string();
        assert!(error.contains("data_location_missing"));
        assert!(error.contains(&target.display().to_string()));
        assert!(!target.exists());
        assert!(!default.exists());
        fs::create_dir_all(&target).unwrap();
        assert!(resolve_data_location(&default).is_err());
        fs::write(target.join("amadeus-data.json"), r#"{"format":1}"#).unwrap();
        assert_eq!(resolve_data_location(&default).unwrap(), target);
        for broken in ["{broken", r#"{"path":"relative"}"#, r#"{}"#] {
            fs::write(&file, broken).unwrap();
            assert!(resolve_data_location(&default).is_err());
            assert!(!default.exists());
        }
        fs::remove_dir_all(base).unwrap();
    }

    #[test]
    fn ready_parser_checks_version_pid_and_port() {
        assert_eq!(parse_backend_ready(
            "AMADEUS_BACKEND_READY {\"version\":1,\"port\":54321,\"pid\":42}\n", 42), Ok(54321));
        for (json, expected) in [
            (r#"{"version":2,"port":54321,"pid":42}"#, "READY version mismatch"),
            (r#"{"version":1,"port":54321,"pid":43}"#, "READY pid mismatch"),
            (r#"{"version":1,"port":0,"pid":42}"#, "READY port out of range"),
            (r#"{"version":1,"port":65536,"pid":42}"#, "READY port out of range"),
            ("{broken", "READY invalid JSON"),
        ] {
            assert_eq!(parse_backend_ready(&format!("{READY_PREFIX}{json}"), 42), Err(expected.into()));
        }
        assert_eq!(parse_backend_ready(r#"{"version":1,"port":54321,"pid":42}"#, 42),
            Err("READY prefix missing".into()));
    }

    #[test]
    fn stdout_is_preserved_and_only_first_ready_counts() {
        let output = b"access log\nAMADEUS_BACKEND_READY {\"version\":1,\"port\":1234,\"pid\":42}\nAMADEUS_BACKEND_READY broken\nmore logs\n";
        let (sender, receiver) = mpsc::channel();
        let mut log = Vec::new();
        read_backend_stdout(&output[..], &mut log, 42, sender);
        assert_eq!(log, output);
        assert_eq!(receiver.recv().unwrap(), Ok(1234));
        assert!(receiver.recv().is_err());
    }

    #[test]
    fn stdout_eof_is_failure_even_when_log_writes_fail() {
        struct BrokenLog;
        impl Write for BrokenLog {
            fn write(&mut self, _: &[u8]) -> io::Result<usize> { Err(io::ErrorKind::Other.into()) }
            fn flush(&mut self) -> io::Result<()> { Ok(()) }
        }
        let mut output = &b"access log\nmore logs\n"[..];
        let (sender, receiver) = mpsc::channel();
        read_backend_stdout(&mut output, BrokenLog, 42, sender);
        assert!(output.is_empty());
        assert_eq!(receiver.recv().unwrap(), Err("stdout EOF before READY".into()));
    }

    fn unique_test_dir(tag: &str) -> PathBuf {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        #[cfg(target_os = "macos")]
        let base = fs::canonicalize(std::env::temp_dir()).unwrap_or_else(|_| std::env::temp_dir());
        #[cfg(not(target_os = "macos"))]
        let base = std::env::temp_dir();
        base.join(format!("amadeus-diag-{tag}-{}-{nonce}", std::process::id()))
    }

    #[test]
    fn redact_sensitive_scrubs_home_directories_and_preserves_http_urls() {
        let input = concat!(
            "profile=C:\\Users\\Jane Doe\\AppData\\Local\\Amadeus\n",
            "case=c:\\users\\jane doe\\Desktop\n",
            "json=\"C:\\\\Users\\\\Jane Doe\\\\data\"\n",
            "fwd=C:/Users/Jane Doe/cache\n",
            "other_win=D:\\Users\\张三\\Documents\\file.txt\n",
            "posix=/home/alice/.config/amadeus and /Users/bob/Library/Logs\n",
            "mac=\"/Users/bob/Desktop\"\n",
            "safe_url=https://example.com/home/docs\n",
        );
        let redacted = redact_sensitive_with_homes(
            input,
            &[],
            Some(r"C:\Users\Jane Doe"),
            Some("/home/alice"),
        );
        assert!(!redacted.contains("Jane Doe"));
        assert!(!redacted.contains("jane doe"));
        assert!(!redacted.contains("张三"));
        assert!(!redacted.contains("/home/alice"));
        assert!(!redacted.contains("/Users/bob"));
        assert!(redacted.contains("%USERPROFILE%"));
        assert!(redacted.contains("<HOME>"));
        assert!(redacted.contains("D:\\Users\\<USER>\\Documents\\file.txt"));
        assert!(redacted.contains("/Users/<USER>/Library/Logs"));
        assert!(redacted.contains("https://example.com/home/docs"));
    }

    #[test]
    fn redact_sensitive_scrubs_tokens_keys_headers_and_preserves_safe_identifiers() {
        let hex_token = "a1b2c3d4e5f60718293a4b5c6d7e8f90112233445566778899aabbccddeeff00";
        let input = format!(
            concat!(
                "local={local}\n",
                "auth=Authorization: Bearer jwt-secret-12345\n",
                "json_auth=\"Authorization\": \"Bearer jwt-json-67890\"\n",
                "basic=Authorization: Basic dXNlcjpwYXNz\n",
                "hdr=x-amadeus-token: amadeus-hdr-secret\n",
                "q=https://user:s3cr3t@proxy.local:8080/v1?token=querytok123&ok=1\n",
                "kv=api_key=super-secret password=\"pass word!\"\n",
                "keys=sk-proj1234567890 AIzaSyDummyKey1234567890 ghp_1234567890ab gho_1234567890ab github_pat_1234567890ab\n",
                "hex={hex_token}\n",
                "safe=disk-1234567890 token_count=42 max_tokens=512\n",
            ),
            local = "live-local-token-xyz",
            hex_token = hex_token,
        );
        let redacted = redact_sensitive_with_homes(&input, &["live-local-token-xyz"], None, None);
        for forbidden in [
            "live-local-token-xyz",
            "jwt-secret-12345",
            "jwt-json-67890",
            "dXNlcjpwYXNz",
            "amadeus-hdr-secret",
            "user:s3cr3t",
            "querytok123",
            "super-secret",
            "pass word!",
            "sk-proj1234567890",
            "AIzaSyDummyKey1234567890",
            "ghp_1234567890ab",
            "gho_1234567890ab",
            "github_pat_1234567890ab",
            hex_token,
        ] {
            assert!(
                !redacted.contains(forbidden),
                "leaked secret {forbidden:?} in {redacted}"
            );
        }
        assert!(redacted.contains("disk-1234567890"));
        assert!(redacted.contains("token_count=42 max_tokens=512"));
        assert!(redacted.contains("ok=1"));
    }

    #[test]
    fn redact_sensitive_handles_redirected_env_dirs_and_cross_platform_slashes() {
        let input = concat!(
            "local=D:\\Profiles\\Alice\\AppData\\Local\\Amadeus\\logs\\app.log\n",
            "local_fwd=D:/Profiles/Alice/AppData/Local/Amadeus/logs\n",
            "local_json=\"D:\\\\Profiles\\\\Alice\\\\AppData\\\\Local\\\\Amadeus\"\n",
            "roaming=E:\\CustomRoaming\\Alice\\cfg\n",
            "home_dir=D:\\Profiles\\Alice\\Desktop\n",
            "posix_custom=/mnt/custom-home/bob/data\n",
            "posix_escaped=/mnt/custom-home/bob\n",
        );
        let envs = [
            ("D:\\Profiles\\Alice\\AppData\\Local", "%LOCALAPPDATA%"),
            ("E:\\CustomRoaming\\Alice", "%APPDATA%"),
            ("D:\\Profiles\\Alice", "%USERPROFILE%"),
            ("/mnt/custom-home/bob", "<HOME>"),
        ];
        let redacted = redact_sensitive_with_env_paths(input, &[], &envs);
        assert!(!redacted.contains("Alice"));
        assert!(!redacted.contains("bob"));
        assert!(redacted.contains("%LOCALAPPDATA%\\Amadeus\\logs\\app.log"));
        assert!(redacted.contains("%LOCALAPPDATA%/Amadeus/logs"));
        assert!(redacted.contains("%LOCALAPPDATA%\\\\Amadeus"));
        assert!(redacted.contains("%APPDATA%\\cfg"));
        assert!(redacted.contains("%USERPROFILE%\\Desktop"));
        assert!(redacted.contains("<HOME>/data"));
    }

    #[test]
    fn check_chain_with_walks_ancestors_allows_missing_leaf_and_rejects_links() {
        let missing_leaf = PathBuf::from("alpha").join("beta").join("missing.log");
        check_chain_with(&missing_leaf, |anc| {
            if anc == missing_leaf.as_path() {
                Err(io::Error::new(io::ErrorKind::NotFound, "missing leaf"))
            } else {
                Ok(false)
            }
        })
        .expect("missing leaf under clean ancestors should succeed");

        let blocked_ancestor = PathBuf::from("alpha").join("logs");
        let under_link = blocked_ancestor.join("backend.log");
        let err = check_chain_with(&under_link, |anc| {
            Ok(anc == blocked_ancestor.as_path())
        })
        .unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);

        let err_leaf = check_chain_with(&under_link, |anc| {
            Ok(anc == under_link.as_path())
        })
        .unwrap_err();
        assert_eq!(err_leaf.kind(), io::ErrorKind::InvalidInput);
    }

    #[test]
    fn read_log_tail_enforces_line_and_byte_caps_and_utf8_boundaries() {
        let root = unique_test_dir("tail");
        fs::create_dir_all(&root).unwrap();
        let path = root.join("backend-stderr.log");
        assert_eq!(
            read_redacted_log_tail_with_limits(&path, &[], STARTUP_DIAG_READ_WINDOW_BYTES, 80, 8192)
                .unwrap(),
            ""
        );

        let mut content = String::new();
        for idx in 0..120 {
            content.push_str(&format!("line-{idx:03} 中文日志内容 测试边界\r\n"));
        }
        fs::write(&path, &content).unwrap();

        let capped_lines =
            read_redacted_log_tail_with_limits(&path, &[], STARTUP_DIAG_READ_WINDOW_BYTES, 10, 8192)
                .unwrap();
        assert_eq!(capped_lines.lines().count(), 10);
        assert!(capped_lines.contains("line-119"));
        assert!(!capped_lines.contains("line-109"));

        let capped_bytes =
            read_redacted_log_tail_with_limits(&path, &[], STARTUP_DIAG_READ_WINDOW_BYTES, 80, 96)
                .unwrap();
        assert!(capped_bytes.len() <= 96);
        assert!(capped_bytes.contains("line-119"));
        assert!(!capped_bytes.contains("line-000"));

        // Single huge multi-byte line exercises byte-level UTF-8 tail truncation.
        let huge_path = root.join("huge.log");
        let huge = "日志".repeat(200);
        fs::write(&huge_path, huge).unwrap();
        let tail =
            read_redacted_log_tail_with_limits(&huge_path, &[], STARTUP_DIAG_READ_WINDOW_BYTES, 80, 64)
                .unwrap();
        assert!(tail.len() <= 64);
        assert!(!tail.is_empty());

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn read_redacted_log_tail_redacts_secrets_straddling_tail_and_window_boundaries() {
        let root = unique_test_dir("boundary");
        fs::create_dir_all(&root).unwrap();
        let live_token = "9f8e7d6c5b4a392817263544556677889900aabbccddeeff0011223344556677";

        // > 12 KiB log where secrets straddle the 8 KiB tail boundary across multi-line and single-line spans.
        let log_path = root.join("large-boundary.log");
        let mut content = String::new();
        for idx in 0..90 {
            content.push_str(&format!("prefix_line_{idx:03}_safe_padding_value_0123456789abcdef\n"));
        }
        content.push_str(&format!(
            "auth Authorization: Bearer secret_8k_boundary_token_xyz sk-secret8kboundarytoken123 token={live_token} {}\n",
            "tail_span_".repeat(820)
        ));
        assert!(content.len() > 12 * 1024);
        fs::write(&log_path, &content).unwrap();

        let tail = read_redacted_log_tail_with_limits(
            &log_path,
            &[live_token],
            STARTUP_DIAG_READ_WINDOW_BYTES,
            STARTUP_DIAG_MAX_LINES,
            STARTUP_DIAG_MAX_BYTES,
        )
        .unwrap();
        assert!(tail.len() <= STARTUP_DIAG_MAX_BYTES);
        for forbidden in [
            "secret_8k_boundary_token_xyz",
            "sk-secret8kboundarytoken123",
            "secret8kboundarytoken123",
            live_token,
            &live_token[24..],
        ] {
            assert!(!tail.contains(forbidden), "leaked {forbidden:?} in tail");
        }
        assert!(tail.contains("tail_span_"));

        // Seek boundary inside a secret splits off its prefix; window drop discards the partial first line.
        let small_path = root.join("window-split.log");
        let split_doc = format!(
            "{} Authorization: Bearer split_window_secret_99999999\n{}\n",
            "pad_".repeat(20),
            "safe_after_line_".repeat(14),
        );
        fs::write(&small_path, &split_doc).unwrap();
        let small_tail =
            read_redacted_log_tail_with_limits(&small_path, &[], 256, 80, 8192).unwrap();
        assert!(!small_tail.contains("split_window_secret_99999999"));
        assert!(!small_tail.contains("99999999"));
        assert!(small_tail.contains("safe_after_line_"));

        fs::remove_dir_all(root).unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn read_redacted_log_tail_rejects_symlinks_in_leaf_or_ancestor() {
        let root = unique_test_dir("symlink");
        fs::create_dir_all(&root).unwrap();
        let target = root.join("target.log");
        let link = root.join("backend-stdout.log");
        fs::write(&target, "secret\n").unwrap();
        std::os::unix::fs::symlink(&target, &link).unwrap();
        let err = read_redacted_log_tail_with_limits(
            &link,
            &[],
            STARTUP_DIAG_READ_WINDOW_BYTES,
            80,
            8192,
        )
        .unwrap_err();
        assert_eq!(err.kind(), io::ErrorKind::InvalidInput);

        let real_dir = root.join("real_logs");
        fs::create_dir_all(&real_dir).unwrap();
        let dir_link = root.join("logs_link");
        std::os::unix::fs::symlink(&real_dir, &dir_link).unwrap();
        let err_parent = read_redacted_log_tail_with_limits(
            &dir_link.join("nonexistent.log"),
            &[],
            STARTUP_DIAG_READ_WINDOW_BYTES,
            80,
            8192,
        )
        .unwrap_err();
        assert_eq!(err_parent.kind(), io::ErrorKind::InvalidInput);

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn extract_last_error_summary_enriches_generic_eof_with_stderr_and_stdout() {
        let summary = extract_last_error_summary(
            Some("stdout EOF before READY"),
            "AMADEUS_BACKEND_FAILED stdout EOF before READY",
            "starting uvicorn\nAMADEUS_BACKEND_FAILED OSError\n",
            "Traceback (most recent call last):\nOSError: [WinError 10048] Only one usage\n",
        );
        assert!(summary.contains("[WinError 10048]"));
        assert!(summary.contains("AMADEUS_BACKEND_FAILED OSError"));
        assert!(summary.contains("stdout EOF before READY"));
    }

    #[test]
    fn collect_startup_diagnostics_redacts_secrets_and_paths() {
        let root = unique_test_dir("collect");
        let logs = root.join("logs");
        fs::create_dir_all(&logs).unwrap();
        let live_token = "9f8e7d6c5b4a392817263544556677889900aabbccddeeff0011223344556677";
        fs::write(
            logs.join("backend-startup.log"),
            format!("AMADEUS_BACKEND_FAILED token={live_token}\n"),
        )
        .unwrap();
        fs::write(
            logs.join("backend-stderr.log"),
            "OSError: sk-secretkey123456789 Authorization: Bearer secret-bearer\n",
        )
        .unwrap();
        fs::write(
            logs.join("backend-stdout.log"),
            format!("AMADEUS_BACKEND_FAILED {live_token}\n"),
        )
        .unwrap();

        let diag = collect_startup_diagnostics(
            &root,
            "0.1.0",
            Some("stdout EOF before READY"),
            &[live_token],
        );
        assert_eq!(diag.app_version, "0.1.0");
        assert_eq!(
            diag.log_dir,
            redact_sensitive(&logs.to_string_lossy(), &[live_token])
        );
        assert_eq!(
            diag.runtime_dir,
            redact_sensitive(&root.to_string_lossy(), &[live_token])
        );
        for field in [
            &diag.startup_log_tail,
            &diag.stderr_log_tail,
            &diag.stdout_log_tail,
            &diag.last_error_summary,
        ] {
            assert!(!field.contains(live_token));
            assert!(!field.contains("sk-secretkey123456789"));
            assert!(!field.contains("secret-bearer"));
        }

        let home_diag = collect_startup_diagnostics(
            Path::new("/home/alice_ci/amadeus-nonexistent"),
            "0.1.0",
            None,
            &[],
        );
        assert!(!home_diag.log_dir.contains("alice_ci"));
        assert!(!home_diag.runtime_dir.contains("alice_ci"));
        assert_eq!(
            home_diag.log_dir.replace('\\', "/"),
            "/home/<USER>/amadeus-nonexistent/logs"
        );

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn read_log_window_drops_unbroken_delimiterless_oversize_fragment() {
        let root = unique_test_dir("delimiterless");
        fs::create_dir_all(&root).unwrap();

        let unbroken_path = root.join("unbroken.log");
        fs::write(&unbroken_path, "A".repeat(128)).unwrap();
        assert_eq!(read_log_window(&unbroken_path, 64).unwrap(), "");

        let token_path = root.join("unbroken-secret.log");
        let x_pad = "X".repeat(80);
        let unbroken_secret = [
            x_pad.as_str(),
            "api_key=",
            "unbrokenSecret123456",
            "sk-",
            "tailPart9999",
        ]
        .concat();
        fs::write(&token_path, unbroken_secret).unwrap();
        assert_eq!(read_log_window(&token_path, 64).unwrap(), "");
        let token_tail =
            read_redacted_log_tail_with_limits(&token_path, &[], 64, 80, 8192).unwrap();
        assert_eq!(token_tail, "");

        let long_ws_path = root.join("long-ws.log");
        fs::write(&long_ws_path, format!("{} SAFE_TAIL", "A".repeat(9000))).unwrap();
        assert_eq!(read_log_window(&long_ws_path, 8192).unwrap(), "SAFE_TAIL");

        let cut_long_path = root.join("cut-secret-long-ws.log");
        let p_pad = "P".repeat(20);
        let y_pad = "Y".repeat(8180);
        let cut_doc = [
            "prefix_",
            p_pad.as_str(),
            "sk-",
            "cutSecret8888",
            y_pad.as_str(),
            " SAFE_TAIL",
        ]
        .concat();
        fs::write(&cut_long_path, cut_doc).unwrap();
        let cut_window = read_log_window(&cut_long_path, 8192).unwrap();
        assert_eq!(cut_window, "SAFE_TAIL");
        let cut_tail =
            read_redacted_log_tail_with_limits(&cut_long_path, &[], 8192, 80, 8192).unwrap();
        assert_eq!(cut_tail, "SAFE_TAIL");

        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn ensure_main_window_label_allows_only_main() {
        assert!(ensure_main_window_label("main").is_ok());
        assert!(ensure_main_window_label("").is_err());
        assert!(ensure_main_window_label("settings").is_err());
        assert!(ensure_main_window_label("MAIN").is_err());
    }

    #[test]
    fn picker_single_flight_guard_rejects_overlap_and_releases_on_drop() {
        let guard = try_acquire_picker_guard().expect("first picker guard must succeed");
        assert!(
            try_acquire_picker_guard().is_none(),
            "concurrent picker guard must be rejected"
        );
        drop(guard);
        let reacquired = try_acquire_picker_guard().expect("picker guard must release on drop");
        drop(reacquired);
    }

    #[test]
    fn parse_picker_kind_accepts_only_directory_and_zip() {
        assert_eq!(parse_picker_kind("directory"), Ok(PickerKind::Directory));
        assert_eq!(parse_picker_kind("zip"), Ok(PickerKind::Zip));
        assert!(parse_picker_kind("file").is_err());
        assert!(parse_picker_kind("").is_err());
    }

    #[cfg(all(target_arch = "x86_64", target_os = "windows"))]
    #[test]
    fn win32_picker_ffi_structs_match_x64_abi() {
        assert_eq!(std::mem::size_of::<win32_picker::BROWSEINFOW>(), 64);
        assert_eq!(std::mem::size_of::<win32_picker::OPENFILENAMEW>(), 152);
    }
}
