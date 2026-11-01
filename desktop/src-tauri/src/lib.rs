use std::fs::{self, File};
use std::io;
#[cfg(not(windows))]
use std::io::Read;
use std::net::TcpListener;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent};

#[cfg(windows)]
use std::os::windows::process::CommandExt;

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
fn get_local_backend(
    window: tauri::WebviewWindow,
    backend: tauri::State<'_, LocalBackend>,
) -> Result<LocalBackend, &'static str> {
    if window.label() != "main" {
        return Err("local backend is only available to the main window");
    }
    Ok(backend.inner().clone())
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

fn start_backend(app: &tauri::AppHandle, token: &str) -> Result<BackendProcess, Box<dyn std::error::Error>> {
    let data_dir = match std::env::var_os("AMADEUS_DATA_DIR").filter(|value| !value.is_empty()) {
        Some(path) => PathBuf::from(path),
        None => app.path().app_local_data_dir()?.join("data"),
    };
    let logs = data_dir.join("logs");
    fs::create_dir_all(&logs)?;
    let backend = app.path().resource_dir()?.join("backend/amadeus-backend.exe");
    let mut command = Command::new(backend);
    command
        .env("AMADEUS_DATA_DIR", &data_dir)
        .env("AMADEUS_LOCAL_API_TOKEN", token)
        .env("AMADEUS_PARENT_PID", std::process::id().to_string())
        .env("BACKEND_PORT", "8000")
        .env("PYTHON_DOTENV_DISABLED", "1")
        .stdin(Stdio::piped())
        .stdout(File::create(logs.join("backend-stdout.log"))?)
        .stderr(File::create(logs.join("backend-stderr.log"))?);
    #[cfg(windows)]
    command.creation_flags(0x0800_0000);
    Ok(BackendProcess(Mutex::new(Some(command.spawn()?))))
}

fn show_port_conflict() {
    let message = "本机端口 8000 已被占用，Amadeus 无法启动本地服务";
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
    if TcpListener::bind(("127.0.0.1", 8000)).is_err() {
        show_port_conflict();
        return;
    }
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![get_local_backend])
        .setup(|app| {
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
            let token = generate_local_token()?;
            app.manage(start_backend(app.handle(), &token)?);
            app.manage(LocalBackend {
                version: 1,
                http_origin: "http://127.0.0.1:8000".into(),
                ws_origin: "ws://127.0.0.1:8000".into(),
                token,
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Amadeus")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(backend) = app.try_state::<BackendProcess>() {
                    backend.stop();
                }
            }
        });
}
