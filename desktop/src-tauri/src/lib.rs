use std::fs::{self, File};
use std::io::{self, BufRead, BufReader, Write};
#[cfg(not(windows))]
use std::io::Read;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

#[cfg(windows)]
use std::os::windows::fs::OpenOptionsExt;
#[cfg(windows)]
use std::os::windows::process::CommandExt;

const BACKEND_READY_TIMEOUT: Duration = Duration::from_secs(120);
const READY_PREFIX: &str = "AMADEUS_BACKEND_READY ";

#[derive(Clone, serde::Serialize)]
struct LocalBackend {
    version: u8,
    http_origin: String,
    ws_origin: String,
    token: String,
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
    ready: Condvar,
}

impl BackendStartup {
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
    if let Ok(mut log) = fs::OpenOptions::new().create(true).append(true)
        .open(logs.join("backend-startup.log"))
    {
        let _ = writeln!(log, "AMADEUS_BACKEND_FAILED {reason}");
    }
    startup.finish(Err(reason));
}

// Keep this handle alive until the desktop exits. The file itself may remain on disk.
struct InstanceLock {
    _file: Option<File>,
}

fn acquire_instance_lock(data_dir: &Path) -> io::Result<InstanceLock> {
    fs::create_dir_all(data_dir)?;
    #[cfg(windows)]
    let file = Some(fs::OpenOptions::new().read(true).write(true).create(true)
        .truncate(false).share_mode(0).open(data_dir.join("desktop.lock"))?);
    #[cfg(not(windows))]
    let file = None;
    Ok(InstanceLock { _file: file })
}

struct BackendProcess(Mutex<Option<Child>>);

impl BackendProcess {
    fn stop(&self) {
        if let Some(mut child) = self.0.lock().expect("backend process lock poisoned").take() {
            // Closing the private pipe asks the backend to run its normal shutdown.
            drop(child.stdin.take());
            let deadline = Instant::now() + Duration::from_secs(5);
            while Instant::now() < deadline {
                if matches!(child.try_wait(), Ok(Some(_))) {
                    return;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn backend_data_dir(app: &tauri::AppHandle) -> tauri::Result<PathBuf> {
    Ok(match std::env::var_os("AMADEUS_DATA_DIR").filter(|value| !value.is_empty()) {
        Some(path) => PathBuf::from(path),
        None => app.path().app_local_data_dir()?.join("data"),
    })
}

fn start_backend(
    app: &tauri::AppHandle,
    data_dir: &Path,
    token: String,
    startup: Arc<BackendStartup>,
) -> Result<Arc<BackendProcess>, Box<dyn std::error::Error>> {
    let logs = data_dir.join("logs");
    fs::create_dir_all(&logs)?;
    let stdout_log = File::create(logs.join("backend-stdout.log"))?;
    let backend = app.path().resource_dir()?.join("backend/amadeus-backend.exe");
    let mut command = Command::new(backend);
    command
        .env("AMADEUS_DATA_DIR", &data_dir)
        .env("AMADEUS_LOCAL_API_TOKEN", &token)
        .env("AMADEUS_PARENT_PID", std::process::id().to_string())
        .env("BACKEND_PORT", "0")
        .env("PYTHON_DOTENV_DISABLED", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(File::create(logs.join("backend-stderr.log"))?);
    #[cfg(windows)]
    command.creation_flags(0x0800_0000);
    let mut child = command.spawn()?;
    let started = Instant::now();
    let pid = child.id();
    let stdout = child.stdout.take().expect("stdout was configured as a pipe");
    let backend = Arc::new(BackendProcess(Mutex::new(Some(child))));
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
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![get_local_backend])
        .setup(|app| {
            let data_dir = match backend_data_dir(app.handle()) {
                Ok(path) => path,
                Err(_) => {
                    show_startup_error("无法确定数据目录，Amadeus 无法启动。");
                    std::process::exit(1);
                }
            };
            match acquire_instance_lock(&data_dir) {
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
            if let Some(window) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::Destroyed = event {
                        if handle.webview_windows().is_empty() {
                            handle.exit(0);
                        }
                    }
                });
            }
            let startup = Arc::new(BackendStartup::default());
            app.manage(Arc::clone(&startup));
            let result = generate_local_token()
                .map_err(Into::into)
                .and_then(|token| start_backend(app.handle(), &data_dir, token, Arc::clone(&startup)));
            match result {
                Ok(backend) => { app.manage(backend); }
                Err(error) => fail_backend_startup(&startup, &data_dir.join("logs"),
                    format!("unable to start backend: {error}")),
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Amadeus")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(backend) = app.try_state::<Arc<BackendProcess>>() {
                    backend.stop();
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
